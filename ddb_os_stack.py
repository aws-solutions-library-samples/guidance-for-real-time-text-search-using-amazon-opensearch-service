from aws_cdk import (
    Stack, App, RemovalPolicy, Duration, CfnOutput, aws_ec2 as ec2, aws_dynamodb as dynamodb,
    aws_iam as iam, aws_lambda as lambda_, aws_sqs as sqs, aws_s3_assets as s3_assets,
    aws_opensearchservice as opensearch, aws_lambda_event_sources as eventsources, CustomResource,
    aws_secretsmanager as secretsmanager, aws_logs as logs
)
from constructs import Construct
import os
import uuid

class DynamoDBOpenSearchStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        admin_user = 'opensearch'

        # Create a new secret in Secrets Manager
        admin_password_secret = secretsmanager.Secret(self, "AdminPasswordSecret",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                secret_string_template='{"username":"opensearch"}',
                generate_string_key="password",
                exclude_characters='/@"\'\\'
            )
        )

        # VPC config with DynamoDB and S3 endpoints
        vpc = ec2.Vpc(self, "VPC",
            ip_addresses=ec2.IpAddresses.cidr("10.0.0.0/16"),
            max_azs=2,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="PublicOne",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="PrivateOne",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24
                )
            ],
            gateway_endpoints={
                "S3": ec2.GatewayVpcEndpointOptions(
                    service=ec2.GatewayVpcEndpointAwsService.S3
                ),
                "DYNAMODB": ec2.GatewayVpcEndpointOptions(
                    service=ec2.GatewayVpcEndpointAwsService.DYNAMODB
                ),
            },
            enable_dns_support=True,
            enable_dns_hostnames=True
        )

        # Add interface VPC endpoint for Secrets Manager, needed by wiring Lambda
        secrets_manager_endpoint = ec2.InterfaceVpcEndpoint(self, "SecretsManagerEndpoint",
            vpc=vpc,
            service=ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),
            private_dns_enabled=True
        )

        # Create a log group for VPC Flow Logs
        flow_log_group = logs.LogGroup(self, "FlowLogGroup",
            retention=logs.RetentionDays.ONE_MONTH
        )

        # Create the IAM role for VPC Flow Logs
        flow_log_role = iam.Role(self, "FlowLogRole",
            assumed_by=iam.ServicePrincipal("vpc-flow-logs.amazonaws.com")
        )

        # Add inline policy to the IAM role for VPC Flow Logs
        flow_log_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
                "logs:DescribeLogGroups",
                "logs:DescribeLogStreams"
            ],
            resources=[
                flow_log_group.log_group_arn,
                f"{flow_log_group.log_group_arn}:log-stream:*"
            ]
        ))

        # Create the VPC Flow Logs
        flow_log = ec2.FlowLog(self, "FlowLog",
            resource_type=ec2.FlowLogResourceType.from_vpc(vpc),
            destination=ec2.FlowLogDestination.to_cloud_watch_logs(flow_log_group, flow_log_role),
            traffic_type=ec2.FlowLogTrafficType.ALL
        )

        # IAM Roles

        # DDBStreamToOpenSearch Lambda
        ddb_stream_to_opensearch_lambda_role = iam.Role(self, "DDBStreamToOpenSearchLambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ]
        )

        # DDBBulkToOpenSearch Lambda
        ddb_bulk_to_opensearch_lambda_role = iam.Role(self, "DDBBulkToOpenSearchLambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ]
        )

        # PopulateQueue Lambda
        populate_queue_lambda_role = iam.Role(self, "PopulateQueueLambdaExecutionRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole"),
            ]
        )

        # Wiring Lambda
        wiring_lambda_role = iam.Role(self, "WiringLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole"),
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")
            ]
        )

        admin_password_secret.grant_read(wiring_lambda_role)

        # Jumphost Instance Role
        jumphost_role = iam.Role(self, f'JumpHostInstanceRole',
                        assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
                managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AmazonEC2RoleforSSM"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore")
            ]
        )

        # Upload DynamoDB JSON file to S3 for import
        dynamodb_asset = s3_assets.Asset(self, "DynamoDBImportFile",
            path="assets/dynamodb/initial_import.json.gz"
        )

        # DynamoDB Table imported from S3
        table = dynamodb.Table(self, "DynamoDBTable",
            partition_key=dynamodb.Attribute(name="product_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="helpful_votes#review_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            import_source=dynamodb.ImportSourceSpecification(
                compression_type=dynamodb.InputCompressionType.GZIP,
                input_format=dynamodb.InputFormat.dynamo_db_json(),
                bucket=dynamodb_asset.bucket,
                key_prefix=dynamodb_asset.s3_object_key
            ),
            removal_policy=RemovalPolicy.DESTROY
        )

        # Add permissions for DynamoDB after table creation
        table.grant_read_write_data(ddb_bulk_to_opensearch_lambda_role)
        table.grant_stream_read(ddb_stream_to_opensearch_lambda_role)
        table.grant(populate_queue_lambda_role, "dynamodb:DescribeTable")

        # SQS Queue orchestrates initial bulk load
        queue = sqs.Queue(self, "DDBBulkTrigger",
            visibility_timeout=Duration.seconds(900)
        )

        # Grant SQS write permissions
        queue.grant_send_messages(populate_queue_lambda_role)

        # Lambda Layer
        lambda_layer = lambda_.LayerVersion(self, "MyLambdaLayer",
            code=lambda_.Code.from_asset("assets/lambda/layers/opensearch-py"),
            compatible_runtimes=[
                lambda_.Runtime.PYTHON_3_8,
                lambda_.Runtime.PYTHON_3_9,
                lambda_.Runtime.PYTHON_3_10,
                lambda_.Runtime.PYTHON_3_11,
                lambda_.Runtime.PYTHON_3_12
            ],
            description="A layer containing opensearch-py dependencies for Lambda functions"
        )

        # OpenSearch security group
        opensearch_sg = ec2.SecurityGroup(
            self, "OpenSearchSG",
            vpc=vpc,
            description="Security group for OpenSearch",
            allow_all_outbound=True
        )
        opensearch_sg.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(443),
            description="Allow OpenSearch access from VPC"
        )

        # OpenSearch Domain - single node
        opensearch_domain = opensearch.Domain(
            self, "MyOpenSearchDomain",
            version=opensearch.EngineVersion.OPENSEARCH_2_11,
            capacity={
                "data_node_instance_type": "t3.medium.search",
                "data_nodes": 1,
                "multi_az_with_standby_enabled": False
            },
            ebs={
                "volume_size": 20
            },
            vpc=vpc,
            vpc_subnets=[
                ec2.SubnetSelection(subnets=[vpc.select_subnets(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED).subnets[0]])
            ],
            security_groups=[opensearch_sg],
            removal_policy=RemovalPolicy.DESTROY,
            zone_awareness={
                "enabled": False
            },
            logging={
                "slow_search_log_enabled": True,
                "app_log_enabled": True,
                "slow_index_log_enabled": True
            },
            enforce_https=True,
            node_to_node_encryption=True,
            encryption_at_rest={
                "enabled": True
            },
            use_unsigned_basic_auth=True,
            fine_grained_access_control={
                "master_user_name": admin_user,
                "master_user_password": admin_password_secret.secret_value_from_json("password")
            },
        )

        # Domain access policy, allow bulk and stream roles

        access_policy = iam.PolicyStatement(
            actions=["es:*"],
            principals=[
                iam.ArnPrincipal(ddb_bulk_to_opensearch_lambda_role.role_arn),
                iam.ArnPrincipal(ddb_stream_to_opensearch_lambda_role.role_arn)
            ],
            resources=[opensearch_domain.domain_arn, f"{opensearch_domain.domain_arn}/*"],
            effect=iam.Effect.ALLOW
        )

        opensearch_domain.add_access_policies(access_policy)

        # Add permissions for OpenSearch after domain creation
        opensearch_domain.grant_read_write(ddb_bulk_to_opensearch_lambda_role)
        opensearch_domain.grant_read_write(ddb_stream_to_opensearch_lambda_role)

        # Lambda Functions

        # DDBBulkToOpenSearch Lambda Function
        ddb_bulk_to_opensearch_lambda = lambda_.Function(self, "DDBBulkToOpenSearch",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=lambda_.Code.from_asset("assets/lambda/bulk_replication"),
            environment={
                "TABLE_NAME": table.table_name,
                "OPENSEARCH_ENDPOINT": opensearch_domain.domain_endpoint,
                "INDEX_NAME": "example-index"
            },
            layers=[lambda_layer],
            role=ddb_bulk_to_opensearch_lambda_role,
            vpc=vpc,
            timeout=Duration.minutes(15)
        )

        ddb_bulk_to_opensearch_lambda.node.add_dependency(opensearch_domain)
        ddb_bulk_to_opensearch_lambda.node.add_dependency(queue)
        ddb_bulk_to_opensearch_lambda.node.add_dependency(table)

        # SQS queue feeds bulk function
        ddb_bulk_to_opensearch_lambda.add_event_source(
            eventsources.SqsEventSource(queue)
        )

        # Define the DDBStreamToOpenSearch Lambda Function
        ddb_stream_to_opensearch_lambda = lambda_.Function(self, "DDBStreamToOpenSearch",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=lambda_.Code.from_asset("assets/lambda/ongoing_replication"),
            environment={
                "INDEX_NAME": "example-index",
                "OPENSEARCH_ENDPOINT": opensearch_domain.domain_endpoint
            },
            layers=[lambda_layer],
            role=ddb_stream_to_opensearch_lambda_role,
            vpc=vpc,
            timeout=Duration.minutes(15)
        )

        ddb_stream_to_opensearch_lambda.node.add_dependency(opensearch_domain)
        ddb_stream_to_opensearch_lambda.node.add_dependency(table)

        # DynamoDB stream as event source
        ddb_stream_to_opensearch_lambda.add_event_source(
            eventsources.DynamoEventSource(
                table,
                starting_position=lambda_.StartingPosition.LATEST,
                batch_size=100,
                bisect_batch_on_error=True,
                retry_attempts=10
            )
        )

        # Define the PopulateQueue Lambda Function
        populate_queue_lambda = lambda_.Function(self, "PopulateQueue",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=lambda_.Code.from_asset("assets/lambda/populate_queue"),
            environment={
                "TABLE_NAME": table.table_name,
                "SQS_QUEUE_URL": queue.queue_url,
                "MAX_ITEMS_PER_WORKER": "10000"
            },
            role=populate_queue_lambda_role,
            timeout=Duration.minutes(15)
        )

        # Custom resoure invoking the PopulateQueue function

        custom_resource = CustomResource(self, "TriggerPopulateQueue",
                                         service_token=populate_queue_lambda.function_arn)
        custom_resource.node.add_dependency(queue)
        custom_resource.node.add_dependency(table)

        # Add EC2 permissions to the Wiring Lambda Role
        #wiring_lambda_role.add_to_policy(iam.PolicyStatement(
        #    actions=[
        #        "ec2:CreateNetworkInterface",
        #        "ec2:DescribeNetworkInterfaces",
        #        "ec2:DeleteNetworkInterface"
        #    ],
        #    resources=["*"]
        #))

        # Wiring Lambda Function - configures fine grain access control
        wiring_lambda = lambda_.Function(self, "WiringLambda",
                                         code=lambda_.Code.from_asset('assets/lambda/wiring_lambda'),
                                         runtime=lambda_.Runtime.PYTHON_3_12,
                                         vpc=vpc,
                                         handler='lambda_function.lambda_handler',
                                         timeout=Duration.seconds(300),
                                         role=wiring_lambda_role
                                        )
        wiring_lambda.add_environment('admin_user', admin_user)
        wiring_lambda.add_environment('secret_arn', admin_password_secret.secret_arn)
        wiring_lambda.add_environment('index_name', "example-index")
        wiring_lambda.add_environment('endpoint', opensearch_domain.domain_endpoint)
        wiring_lambda.add_environment('bulk_role_arn', ddb_bulk_to_opensearch_lambda_role.role_arn)
        wiring_lambda.add_environment('streaming_role_arn', ddb_stream_to_opensearch_lambda_role.role_arn)

        # Custom resoure invoking the Wiring function

        wiring_lambda_resource = CustomResource(self,
                                                "WiringResource",
                                                service_token=wiring_lambda.function_arn)
        wiring_lambda_resource.node.add_dependency(opensearch_domain)
        wiring_lambda_resource.node.add_dependency(vpc)
        wiring_lambda_resource.node.add_dependency(secrets_manager_endpoint)

        # EC2 Jump host - proxy for OpenSearch Dashboards

        # Jump host for and direct access 
  
        instance = ec2.Instance(self, f'JumpHost',
            instance_type=ec2.InstanceType('t3.medium'),
            vpc=vpc,
            block_devices=[ec2.BlockDevice(
                device_name="/dev/sdh",
                volume=ec2.BlockDeviceVolume.ebs(10,
                    encrypted=True
                )
            )],
            machine_image=ec2.MachineImage.latest_amazon_linux2(),
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            role=jumphost_role,
        )
        instance.connections.allow_from_any_ipv4(ec2.Port.tcp(443), 'HTTPS')

        stmt = iam.PolicyStatement(actions=['es:*'],
                                   resources=[opensearch_domain.domain_arn])
        instance.add_to_role_policy(stmt)

        dirname = os.path.dirname(__file__)
        nginx_asset = s3_assets.Asset(self, "NginxAsset", path=os.path.join(dirname, 'nginx_opensearch.conf'))
        nginx_asset.grant_read(instance.role)
        nginx_asset_path = instance.user_data.add_s3_download_command(
            bucket=nginx_asset.bucket,
            bucket_key=nginx_asset.s3_object_key,
        )

        instance.user_data.add_commands(
            "yum update -y",
            "yum install jq -y",

            "amazon-linux-extras install nginx1.12",
            "mkdir -p /home/ec2-user/assets",
            "cd /home/ec2-user/assets",
            f"mv {nginx_asset_path} nginx_opensearch.conf",
            "openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout /etc/nginx/cert.key -out /etc/nginx/cert.crt -subj /C=US/ST=./L=./O=./CN=.\n"
            "cp nginx_opensearch.conf /etc/nginx/conf.d/",
            f"sed -i 's/DOMAIN_ENDPOINT/{opensearch_domain.domain_endpoint}/g' /etc/nginx/conf.d/nginx_opensearch.conf",
            "systemctl restart nginx.service",
        )

        # Outputs
        CfnOutput(self, "OpenSearchDomainEndpoint",
                  value=opensearch_domain.domain_endpoint)
        CfnOutput(self, "OpenSearchDomainArn",
                  value=opensearch_domain.domain_arn)
        CfnOutput(self, "AdminUser",
                  value=admin_user,
                  description="Master User Name for Amazon OpenSearch Service")
        CfnOutput(self, "AdminPasswordSecretArn",
                  value=admin_password_secret.secret_arn,
                  description="ARN of the Secrets Manager secret for the admin password")
        CfnOutput(self, "Dashboards URL (via Jump host)",
                  value=f'https://{instance.instance_public_ip}',
                  description="Dashboards URL via Jump host")
        CfnOutput(self, "DynamoDBTableName",
                  value=table.table_name,
                  description="Name of the DynamoDB table")

app = App()
DynamoDBOpenSearchStack(app, "DynamoDBOpenSearchStack")
app.synth()
