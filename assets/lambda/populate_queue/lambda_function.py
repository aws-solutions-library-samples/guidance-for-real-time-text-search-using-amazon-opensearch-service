import cfnresponse
import logging
import os
import signal
import traceback
import json
import math
import boto3

LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

sqs = boto3.client('sqs')
dynamodb = boto3.client('dynamodb')

table_name = os.environ['TABLE_NAME']
sqs_queue_url = os.environ['SQS_QUEUE_URL']
max_items_per_worker = int(os.environ['MAX_ITEMS_PER_WORKER'])

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
            LOGGER.info('CREATE!')

            params = {
                'TableName': table_name
            }
            data = dynamodb.describe_table(**params)
            total_items = data['Table']['ItemCount']
            segments = max(math.floor(total_items / max_items_per_worker), 100)
            batch_size = 10
            for i in range(0, segments, batch_size):
                entries = [
                    {
                        'Id': str(i + j),
                        'MessageBody': json.dumps({
                            'Segment': i + j,
                            'TotalSegments': segments,
                            'TableName': table_name,
                        })
                    }
                    for j in range(min(batch_size, segments - i))
                ]
                response = sqs.send_message_batch(
                    QueueUrl=sqs_queue_url,
                    Entries=entries
                )
                LOGGER.info('Batch response: %s', response)
            LOGGER.info('[LAUNCHER SUCCESS] - created %d messages for table %s', segments, table_name)
            responseData = {'Message': 'Create Success'}
            cfnresponse.send(event, context, cfnresponse.SUCCESS, responseData)

    except Exception as err:
        AccountRegionInfo = f'Occurred in Account {context.invoked_function_arn.split(":")[4]} in region {context.invoked_function_arn.split(":")[3]}'
        FinalMsg = str(err) + ' ' + AccountRegionInfo
        LOGGER.error('ERROR: %s', FinalMsg)
        LOGGER.error('TRACEBACK %s', traceback.format_exc())
        cfnresponse.send(event, context, "FAILED", {
            "Message": f"{FinalMsg}"})

def timeout_handler(_signal, _frame):
    raise Exception('Time exceeded')

signal.signal(signal.SIGALRM, timeout_handler)
