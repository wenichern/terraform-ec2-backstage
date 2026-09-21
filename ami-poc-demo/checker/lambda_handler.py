"""AWS Lambda entry point: scan for outdated AMIs and publish CloudWatch metrics."""
import json
import os

import ami_checker as ac


def handler(event, context):
    region = os.environ["AWS_REGION"]  # set automatically by Lambda
    ssm_param = os.environ.get("LATEST_AMI_SSM_PARAM", ac.DEFAULT_SSM_PARAM)
    results = ac.scan(region, ssm_param)
    ac.publish(region, results)
    summary = {
        "scanned": len(results),
        "outdated": [r for r in results if r["status"] != "ok"],
    }
    print(json.dumps(summary))
    return summary
