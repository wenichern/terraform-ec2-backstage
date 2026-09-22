"""Detect running EC2 instances whose AMI is deprecated, missing, or not the latest.

Read-only against EC2 and SSM. With --publish it also writes one CloudWatch metric
per instance (0 ok, 1 newer AMI available, 2 deprecated/missing) so Grafana can
alert, and so the alert clears after the instance is replaced.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

DEFAULT_SSM_PARAM = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
NAMESPACE = "SRE/AmiDrift"
ENTITY_TAG = "backstage-entity"          # e.g. component:default/payments-api
SEVERITY = {"ok": 0, "drift": 1, "eol": 2, "missing": 2}


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def classify(image, latest_ami_id, now):
    """Pure function: returns (status, reason). status: ok | drift | eol | missing."""
    if image is None:
        return "missing", "AMI no longer exists or is not accessible"
    dep = parse_time(image.get("DeprecationTime"))
    if dep and dep <= now:
        return "eol", f"AMI deprecated on {dep:%Y-%m-%d}"
    if image["ImageId"] != latest_ami_id:
        return "drift", "A newer AMI is available"
    return "ok", "Running the latest AMI"


def _describe_images(ec2, ids):
    if not ids:
        return {}
    try:
        return {i["ImageId"]: i for i in ec2.describe_images(ImageIds=ids)["Images"]}
    except ClientError:  # one bad ID fails the whole call, so retry one by one
        found = {}
        for image_id in ids:
            try:
                for i in ec2.describe_images(ImageIds=[image_id])["Images"]:
                    found[i["ImageId"]] = i
            except ClientError:
                pass
        return found


def scan(region, ssm_param=DEFAULT_SSM_PARAM, include_untagged=False, now=None):
    now = now or datetime.now(timezone.utc)
    ec2 = boto3.client("ec2", region_name=region)
    ssm = boto3.client("ssm", region_name=region)
    latest = ssm.get_parameter(Name=ssm_param)["Parameter"]["Value"]

    filters = [{"Name": "instance-state-name", "Values": ["running"]}]
    if not include_untagged:
        filters.append({"Name": "tag-key", "Values": [ENTITY_TAG]})
    instances = [i for page in ec2.get_paginator("describe_instances").paginate(Filters=filters)
                 for r in page["Reservations"] for i in r["Instances"]]

    images = _describe_images(ec2, sorted({i["ImageId"] for i in instances}))
    results = []
    for i in instances:
        tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
        image = images.get(i["ImageId"])
        status, reason = classify(image, latest, now)
        results.append({
            "instance_id": i["InstanceId"],
            "name": tags.get("Name", ""),
            "service": tags.get(ENTITY_TAG, ""),
            "region": region,
            "current_ami": i["ImageId"],
            "current_ami_name": (image or {}).get("Name", ""),
            "latest_ami": latest,
            "status": status,
            "reason": reason,
        })
    return results


def publish(region, results):
    """Publish two shapes of the same metric:
    - per-instance (InstanceId + Service dims): for investigation / the chat tool.
    - per-service aggregate (Service dim only, MAX across its instances): for ASG-level
      alarms, since CloudWatch alarms require an exact dimension-set match and an ASG
      alarm has no single InstanceId to key on.
    """
    cw = boto3.client("cloudwatch", region_name=region)
    per_instance = [{
        "MetricName": "AmiStatus",
        "Dimensions": [{"Name": "InstanceId", "Value": r["instance_id"]},
                       {"Name": "Service", "Value": r["service"] or "unknown"}],
        "Value": SEVERITY[r["status"]],
        "Unit": "None",
    } for r in results]

    by_service = {}
    for r in results:
        svc = r["service"] or "unknown"
        by_service[svc] = max(by_service.get(svc, 0), SEVERITY[r["status"]])
    per_service = [{
        "MetricName": "AmiStatus",
        "Dimensions": [{"Name": "Service", "Value": svc}],
        "Value": value,
        "Unit": "None",
    } for svc, value in by_service.items()]

    data = per_instance + per_service
    for start in range(0, len(data), 500):
        cw.put_metric_data(Namespace=NAMESPACE, MetricData=data[start:start + 500])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    p.add_argument("--ssm-param", default=os.getenv("LATEST_AMI_SSM_PARAM", DEFAULT_SSM_PARAM))
    p.add_argument("--all", action="store_true", help=f"include instances without the {ENTITY_TAG} tag")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument("--publish", action="store_true", help="publish CloudWatch metrics")
    p.add_argument("--fail-on-outdated", action="store_true", help="exit 2 if any instance is not ok")
    a = p.parse_args()

    results = scan(a.region, a.ssm_param, include_untagged=a.all)
    if a.publish:
        publish(a.region, results)
    if a.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"{r['status']:8} {r['instance_id']}  {r['service'] or '-':40} {r['current_ami']} -> {r['latest_ami']}  {r['reason']}")
        if not results:
            print("No matching running instances")
    if a.fail_on_outdated and any(r["status"] != "ok" for r in results):
        sys.exit(2)


if __name__ == "__main__":
    main()
