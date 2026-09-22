"""Exercises the MCP tools directly (no live server) against mocked AWS."""
from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws

import server as srv

PARAM = "/test/latest-ami"


def _setup(region="us-east-1"):
    ec2 = boto3.client("ec2", region_name=region)
    old = ec2.register_image(Name="old-ami")["ImageId"]
    new = ec2.register_image(Name="new-ami")["ImageId"]
    boto3.client("ssm", region_name=region).put_parameter(Name=PARAM, Value=new, Type="String")
    tag = [{"Key": "backstage-entity", "Value": "component:default/payments-api"}, {"Key": "Name", "Value": "p"}]
    old_id = ec2.run_instances(ImageId=old, MinCount=1, MaxCount=1, TagSpecifications=[
        {"ResourceType": "instance", "Tags": tag}])["Instances"][0]["InstanceId"]
    new_id = ec2.run_instances(ImageId=new, MinCount=1, MaxCount=1, TagSpecifications=[
        {"ResourceType": "instance", "Tags": tag}])["Instances"][0]["InstanceId"]
    return old_id, new_id


@pytest.mark.asyncio
async def test_list_outdated_ec2(monkeypatch):
    monkeypatch.setattr(srv, "SSM_PARAM", PARAM)
    with mock_aws():
        old_id, new_id = _setup()

        out = await srv.list_outdated_ec2(service="", region="us-east-1")
        assert old_id in out and "drift" in out
        assert new_id not in out  # only outdated ones are listed

        out_filtered = await srv.list_outdated_ec2(service="component:default/payments-api", region="us-east-1")
        assert old_id in out_filtered

        out_missing = await srv.list_outdated_ec2(service="component:default/nonexistent", region="us-east-1")
        assert "up to date" in out_missing or "0 scanned" in out_missing or "All 0" in out_missing


@pytest.mark.asyncio
async def test_all_up_to_date_message(monkeypatch):
    monkeypatch.setattr(srv, "SSM_PARAM", PARAM)
    with mock_aws():
        region = "us-east-1"
        ec2 = boto3.client("ec2", region_name=region)
        new = ec2.register_image(Name="new-ami")["ImageId"]
        boto3.client("ssm", region_name=region).put_parameter(Name=PARAM, Value=new, Type="String")
        ec2.run_instances(ImageId=new, MinCount=1, MaxCount=1, TagSpecifications=[
            {"ResourceType": "instance", "Tags": [{"Key": "backstage-entity", "Value": "component:default/x"}]}])

        out = await srv.list_outdated_ec2(region=region)
        assert "latest AMI" in out


@pytest.mark.asyncio
async def test_check_ec2_ami_found_and_not_found(monkeypatch):
    monkeypatch.setattr(srv, "SSM_PARAM", PARAM)
    with mock_aws():
        old_id, new_id = _setup()

        out = await srv.check_ec2_ami(instance_id=old_id, region="us-east-1")
        assert old_id in out and "drift" in out

        out2 = await srv.check_ec2_ami(instance_id="i-doesnotexist", region="us-east-1")
        assert "not found" in out2
