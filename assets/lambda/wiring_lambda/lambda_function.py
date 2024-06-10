import cfnresponse
import logging
import os
import signal
import traceback
import boto3
import json
from opensearchpy import OpenSearch

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

def lambda_handler(event, context):
    # Setup alarm for remaining runtime minus a second
    signal.alarm((int(context.get_remaining_time_in_millis() / 1000)) - 1)
    try:
        LOGGER.info('REQUEST RECEIVED: %s', event)
        LOGGER.info('REQUEST RECEIVED: %s', context)
        if event['RequestType'] == 'Delete':
            LOGGER.info('DELETE!')
            cfnresponse.send(event, context, "SUCCESS", {
                "Message": "Resource deletion successful!"})
            return
        elif event['RequestType'] == 'Update':
            LOGGER.info('UPDATE!')
            cfnresponse.send(event, context, "SUCCESS",{
                "Message": "Resource update successful!"})
        elif event['RequestType'] == 'Create':

            # Fetch the admin password from Secrets Manager
            secret_arn = os.environ['secret_arn']
            secret_client = boto3.client('secretsmanager')
            secret_response = secret_client.get_secret_value(SecretId=secret_arn)
            secret_string = secret_response['SecretString']
            secret = json.loads(secret_string)
            admin_pw = secret['password']
            
            client = OpenSearch(
                hosts = [{'host': f'{os.environ["endpoint"]}', 'port': 443}],
                http_compress = True, # enables gzip compression for request bodies
                http_auth = (f'{os.environ["admin_user"]}', admin_pw),
                use_ssl = True,
                verify_certs = False,
                ssl_assert_hostname = False,
                ssl_show_warn = False,
            )

            fgac_cluster_perms = {
                "cluster_permissions": [
                    "cluster_monitor",
                    "cluster_composite_ops"
                ],
                "index_permissions": [{
                    "index_patterns": [
                        f'{os.environ["index_name"]}'
                    ],
                    "dls": "",
                    "fls": [],
                    "masked_fields": [],
                    "allowed_actions": [
                        "indices:admin/exists",
                        "indices:admin/create",
                        "crud"
                    ]
                }]
            }

            fgac_cluster_mapping_bulk = {"backend_roles" : [ f'{os.environ["bulk_role_arn"]}' ]}
            fgac_cluster_mapping_streaming = {"backend_roles" : [ f'{os.environ["streaming_role_arn"]}' ]}

            cr_resp_bulk = client.security.create_role(role='bulk_write_role', body=fgac_cluster_perms)
            LOGGER.info(f'Create role response (bulk): {cr_resp_bulk}')
            rm_resp_bulk = client.security.create_role_mapping(role='bulk_write_role', body=fgac_cluster_mapping_bulk)
            LOGGER.info(f'Role mapping response (bulk): {rm_resp_bulk}')

            cr_resp_streaming = client.security.create_role(role='streaming_write_role', body=fgac_cluster_perms)
            LOGGER.info(f'Create role response (streaming): {cr_resp_streaming}')
            rm_resp_streaming = client.security.create_role_mapping(role='streaming_write_role', body=fgac_cluster_mapping_streaming)
            LOGGER.info(f'Role mapping response (streaming): {rm_resp_streaming}')

            responseData = {}
            responseData['create_role_response_bulk'] = cr_resp_bulk
            responseData['put_role_mapping_response_bulk'] = rm_resp_bulk
            responseData['create_role_response_streaming'] = cr_resp_streaming
            responseData['put_role_mapping_response_streaming'] = rm_resp_streaming
            cfnresponse.send(event, context, cfnresponse.SUCCESS, responseData)

    except Exception as err:
        AccountRegionInfo=f'Occurred in Account {{context.invoked_function_arn.split(":")[4]}} in region {{context.invoked_function_arn.split(":")[3]}}'
        FinalMsg=str(err) + ' ' + AccountRegionInfo
        LOGGER.info('ERROR: %s', FinalMsg)
        LOGGER.info('TRACEBACK %s', traceback.print_tb(err.__traceback__))
        cfnresponse.send(event, context, "FAILED", {
            "Message": f"{FinalMsg}"})

def timeout_handler(_signal, _frame):
    raise Exception('Time exceeded')

signal.signal(signal.SIGALRM, timeout_handler)
