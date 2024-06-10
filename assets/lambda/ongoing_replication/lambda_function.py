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


def translate_dynamodb_type(dynamodb_value):
    if 'S' in dynamodb_value:
        return dynamodb_value['S']
    elif 'N' in dynamodb_value:
        return dynamodb_value['N']
    elif 'BOOL' in dynamodb_value:
        return dynamodb_value['BOOL']
    elif 'B' in dynamodb_value:
        return dynamodb_value['B']
    elif 'SS' in dynamodb_value:
        return dynamodb_value['SS']
    elif 'NS' in dynamodb_value:
        return dynamodb_value['NS']
    elif 'BS' in dynamodb_value:
        return dynamodb_value['BS']
    elif 'L' in dynamodb_value:
        return [translate_dynamodb_type(v) for v in dynamodb_value['L']]
    elif 'M' in dynamodb_value:
        return {k: translate_dynamodb_type(v) for k, v in dynamodb_value['M'].items()}
    else:
        return str(dynamodb_value)


def process_stream_records(records):
    actions = []
    for record in records:
        if record['eventName'] in ['INSERT', 'MODIFY']:
            new_image = record['dynamodb']['NewImage']
            product_id = new_image.get('product_id', {}).get('S', '')
            review_id = new_image.get('helpful_votes#review_id', {}).get('S', '')
            document_id = f"{product_id}#{review_id}"

            action = {
                "_op_type": "index",
                "_index": index_name,
                "_id": document_id,
                "_source": {k: translate_dynamodb_type(v) for k, v in new_image.items()}
            }
            actions.append(action)
        elif record['eventName'] == 'REMOVE':
            old_image = record['dynamodb']['OldImage']
            product_id = old_image.get('product_id', {}).get('S', '')
            review_id = old_image.get('helpful_votes#review_id', {}).get('S', '')
            document_id = f"{product_id}#{review_id}"
            
            action = {
                "_op_type": "delete",
                "_index": index_name,
                "_id": document_id
            }
            actions.append(action)

    if actions:
        try:
            resp = bulk(client, actions)
        except Exception as e:
            message, errors = e.args
            for error in errors:
                status = error['index']['status'] if 'index' in error else error['delete']['status']
                if status != 409:
                    raise OpenSearchError(f"Error in OpenSearch ingestion with status: {status}")

    return len(actions)


def lambda_handler(event, context):
    try:
        record_count = process_stream_records(event['Records'])
        print(f'[OpenSearch Ingestion Success] - processed {record_count} records')
        return {'statusCode': 200}
    except Exception as e:
        print(f'[OpenSearch Ingestion Error] - an error occurred: {str(e)}')
        raise e
        return {'statusCode': 500}