from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
from opensearchpy.helpers import bulk
import boto3
import os
import json
from datetime import datetime

# Fetch values from the environment variables
host = os.getenv('OPENSEARCH_ENDPOINT')
region = os.getenv('AWS_REGION')
service = os.getenv('AWS_SERVICE', 'es')
credentials = boto3.Session().get_credentials()
index_name = os.getenv('INDEX_NAME')

auth = AWSV4SignerAuth(credentials, region, service)

client = OpenSearch(
    hosts=[{'host': host, 'port': 443}],
    http_auth=auth,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection,
    pool_maxsize=20,
    timeout=60
)


class OpenSearchError(Exception):
    """Custom exception for errors during bulk operations in OpenSearch."""
    pass


def process_page(items, actions, force_bulk=False):
    print(f'DDB Page Returned: {datetime.now()}')
    for item in items:
        product_id = item['product_id']
        review_id = item['helpful_votes#review_id']
        document_id = f"{product_id}#{review_id}"
        action = {
            "_op_type": "create",
            "_index": index_name,
            "_id": document_id,
            "_source": item
        }
        actions.append(action)

    if len(actions) >= 20000 or force_bulk:
        try:
            print(str(actions))
            resp = bulk(client, actions)
        except Exception as e:
            message, errors = e.args
            for error in errors:
                status = error['create']['status']
                if status != 409:
                    raise OpenSearchError(f"Error in OpenSearch ingestion with status: {status}")
        actions = []
        print(f'Page processed: {datetime.now()}')

    return actions


def insert_records_bulk(table, index_name, segment, total_segments):
    actions = []  # list to store the actions

    # Initialize the scan operation with Segment and TotalSegments
    response = table.scan(
        Segment=segment,
        TotalSegments=total_segments
    )

    while 'LastEvaluatedKey' in response:
        try:
            actions = process_page(response['Items'], actions)
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'], Segment=segment, TotalSegments=total_segments)
        except OpenSearchError as e:
            print(f'Error during OpenSearch bulk operation: {str(e)}')
            raise

    # Process the last page of items
    try:
        actions = process_page(response['Items'], actions, force_bulk=True)
    except OpenSearchError as e:
        print(f'Error during OpenSearch bulk operation: {str(e)}')
        raise


def lambda_handler(event, context):

    try:
        # Parse SQS message
        for record in event['Records']:
            message = json.loads(record['body'])

            segment = int(message['Segment'])
            total_segments = int(message['TotalSegments'])
            table_name = message['TableName']

            dynamodb = boto3.resource('dynamodb', region_name=region)
            table = dynamodb.Table(table_name)
            insert_records_bulk(table, index_name, segment, total_segments)
            print(f'[OpenSearch Ingestion Success] - ingested segment {segment} of {total_segments} from {table_name}')
        return {'statusCode': 200}
    except Exception as e:
        print(f'[OpenSearch Ingestion Error] - an error occurred: {str(e)}')
        raise e
        return {'statusCode': 500}