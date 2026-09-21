from datetime import datetime, timezone

import boto3
from moto import mock_aws

import ami_checker as ac

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)
PARAM = "/test/latest-ami"  # moto reserves /aws/service/*


def test_classify():
    latest = "ami-new"
    assert ac.classify(None, latest, NOW)[0] == "missing"
    assert ac.classify({"ImageId": "ami-old", "DeprecationTime": "2026-06-30T00:00:00.000Z"}, latest, NOW)[0] == "eol"
    assert ac.classify({"ImageId": "ami-old", "DeprecationTime": "2027-01-01T00:00:00.000Z"}, latest, NOW)[0] == "drift"
    assert ac.classify({"ImageId": "ami-old"}, latest, NOW)[0] == "drift"
    assert ac.classify({"ImageId": latest}, latest, NOW)[0] == "ok"


@mock_aws
def test_scan_and_publish():
    region = "us-east-1"
    ec2 = boto3.client("ec2", region_name=region)
    old = ec2.register_image(Name="old-ami")["ImageId"]
    new = ec2.register_image(Name="new-ami")["ImageId"]
    boto3.client("ssm", region_name=region).put_parameter(Name=PARAM, Value=new, Type="String")

    def run(ami, tags):
        spec = [{"ResourceType": "instance", "Tags": tags}] if tags else []
        return ec2.run_instances(ImageId=ami, MinCount=1, MaxCount=1, TagSpecifications=spec)["Instances"][0]["InstanceId"]

    tag = [{"Key": ac.ENTITY_TAG, "Value": "component:default/payments-api"}, {"Key": "Name", "Value": "payments"}]
    old_id, new_id = run(old, tag), run(new, tag)
    run(old, [])  # untagged: ignored by default

    results = {r["instance_id"]: r for r in ac.scan(region, PARAM)}
    assert set(results) == {old_id, new_id}
    assert results[old_id]["status"] == "drift" and results[old_id]["latest_ami"] == new
    assert results[new_id]["status"] == "ok"
    assert len(ac.scan(region, PARAM, include_untagged=True)) == 3

    ac.publish(region, list(results.values()))
    metrics = boto3.client("cloudwatch", region_name=region).list_metrics(Namespace=ac.NAMESPACE)["Metrics"]
    assert len(metrics) == 2


@mock_aws
def test_lambda_handler(monkeypatch):
    import lambda_handler

    region = "us-east-1"
    monkeypatch.setenv("AWS_REGION", region)
    monkeypatch.setenv("LATEST_AMI_SSM_PARAM", PARAM)
    ec2 = boto3.client("ec2", region_name=region)
    old = ec2.register_image(Name="old-ami")["ImageId"]
    new = ec2.register_image(Name="new-ami")["ImageId"]
    boto3.client("ssm", region_name=region).put_parameter(Name=PARAM, Value=new, Type="String")
    ec2.run_instances(ImageId=old, MinCount=1, MaxCount=1, TagSpecifications=[
        {"ResourceType": "instance", "Tags": [{"Key": ac.ENTITY_TAG, "Value": "component:default/demo"}]}])

    out = lambda_handler.handler({}, None)
    assert out["scanned"] == 1 and out["outdated"][0]["status"] == "drift"
