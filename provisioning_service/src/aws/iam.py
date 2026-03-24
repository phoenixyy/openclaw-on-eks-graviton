"""IAM operations for Pod Identity"""
import boto3
import json
import logging
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

def create_pod_identity_role(user_id, region='us-west-2'):
    """
    Create IAM Role for EKS Pod Identity

    Args:
        user_id: User ID
        region: AWS region

    Returns:
        Role ARN or None if creation failed
    """
    iam = boto3.client('iam', region_name=region)
    role_name = f"openclaw-user-{user_id}"

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {
                    "Service": "pods.eks.amazonaws.com"
                },
                "Action": [
                    "sts:AssumeRole",
                    "sts:TagSession"
                ]
            }
        ]
    }

    try:
        # Create role
        response = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=f"OpenClaw Pod Identity Role for user {user_id}",
            Tags=[
                {'Key': 'user_id', 'Value': user_id},
                {'Key': 'managed_by', 'Value': 'openclaw-provisioning'},
                {'Key': 'cost_allocation', 'Value': f'openclaw-user-{user_id}'}
            ]
        )
        role_arn = response['Role']['Arn']
        logger.info(f"Created IAM Role: {role_arn}")

        # Attach Bedrock policy
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn='arn:aws:iam::aws:policy/AmazonBedrockFullAccess'
        )
        logger.info(f"Attached Bedrock policy to {role_name}")

        return role_arn

    except ClientError as e:
        if e.response['Error']['Code'] == 'EntityAlreadyExists':
            logger.info(f"IAM Role {role_name} already exists")
            # Get existing role ARN
            try:
                response = iam.get_role(RoleName=role_name)
                return response['Role']['Arn']
            except ClientError as get_error:
                logger.error(f"Failed to get existing role: {get_error}")
                return None
        else:
            logger.error(f"Failed to create IAM Role: {e}")
            return None


def delete_pod_identity_role(user_id, region='us-west-2'):
    """
    Delete IAM Role for EKS Pod Identity

    Args:
        user_id: User ID
        region: AWS region

    Returns:
        True if deleted successfully, False otherwise
    """
    iam = boto3.client('iam', region_name=region)
    role_name = f"openclaw-user-{user_id}"

    try:
        # Detach all policies
        response = iam.list_attached_role_policies(RoleName=role_name)
        for policy in response['AttachedPolicies']:
            iam.detach_role_policy(
                RoleName=role_name,
                PolicyArn=policy['PolicyArn']
            )
            logger.info(f"Detached policy {policy['PolicyArn']} from {role_name}")

        # Delete role
        iam.delete_role(RoleName=role_name)
        logger.info(f"Deleted IAM Role: {role_name}")
        return True

    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            logger.info(f"IAM Role {role_name} does not exist")
            return True
        else:
            logger.error(f"Failed to delete IAM Role: {e}")
            return False


def create_pod_identity_association(cluster_name, namespace, service_account, role_arn, region='us-west-2'):
    """
    Create EKS Pod Identity Association

    Args:
        cluster_name: EKS cluster name
        namespace: Kubernetes namespace
        service_account: ServiceAccount name
        role_arn: IAM Role ARN
        region: AWS region

    Returns:
        Association ID or None if creation failed
    """
    eks = boto3.client('eks', region_name=region)

    try:
        response = eks.create_pod_identity_association(
            clusterName=cluster_name,
            namespace=namespace,
            serviceAccount=service_account,
            roleArn=role_arn
        )
        association_id = response['association']['associationId']
        logger.info(f"Created Pod Identity Association: {association_id}")
        return association_id

    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceInUseException':
            logger.info(f"Pod Identity Association already exists for {namespace}/{service_account}")
            # List and find existing association
            try:
                response = eks.list_pod_identity_associations(
                    clusterName=cluster_name,
                    namespace=namespace,
                    serviceAccount=service_account
                )
                if response['associations']:
                    return response['associations'][0]['associationId']
            except ClientError as list_error:
                logger.error(f"Failed to list associations: {list_error}")
        else:
            logger.error(f"Failed to create Pod Identity Association: {e}")
        return None


def delete_pod_identity_association(cluster_name, association_id, region='us-west-2'):
    """
    Delete EKS Pod Identity Association

    Args:
        cluster_name: EKS cluster name
        association_id: Association ID
        region: AWS region

    Returns:
        True if deleted successfully, False otherwise
    """
    eks = boto3.client('eks', region_name=region)

    try:
        eks.delete_pod_identity_association(
            clusterName=cluster_name,
            associationId=association_id
        )
        logger.info(f"Deleted Pod Identity Association: {association_id}")
        return True

    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            logger.info(f"Pod Identity Association {association_id} does not exist")
            return True
        else:
            logger.error(f"Failed to delete Pod Identity Association: {e}")
            return False


def list_pod_identity_associations(cluster_name, namespace, service_account, region='us-west-2'):
    """
    List Pod Identity Associations for a ServiceAccount

    Args:
        cluster_name: EKS cluster name
        namespace: Kubernetes namespace
        service_account: ServiceAccount name
        region: AWS region

    Returns:
        List of association IDs
    """
    eks = boto3.client('eks', region_name=region)

    try:
        response = eks.list_pod_identity_associations(
            clusterName=cluster_name,
            namespace=namespace,
            serviceAccount=service_account
        )
        return [assoc['associationId'] for assoc in response.get('associations', [])]
    except ClientError as e:
        logger.error(f"Failed to list Pod Identity Associations: {e}")
        return []
