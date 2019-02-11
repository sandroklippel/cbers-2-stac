"""es"""

import os
import json
import boto3
from elasticsearch import Elasticsearch, RequestsHttpConnection
from elasticsearch.helpers import bulk
from aws_requests_auth.boto_utils import BotoAWSRequestsAuth

SQS_CLIENT = boto3.client('sqs')

def sqs_messages(queue: str):
    """
    Generator for SQS messages.

    :param queue str: SQS URL
    :return: dict with the following keys:
             stac_item: STAC item, str
             ReceiptHandle: ditto
    :rtype: dict
    """

    while True:
        response = SQS_CLIENT.receive_message(
            QueueUrl=queue)
        if 'Messages' not in response:
            break
        msg = json.loads(response['Messages'][0]['Body'])
        retd = dict()
        retd['stac_item'] = msg['Message']
        retd['ReceiptHandle'] = response['Messages'][0]['ReceiptHandle']
        yield retd


def process_insert_queue(es_client, queue: str,
                         batch_size: int = 1,
                         delete_messages: bool = True):
    """
    Read queue with itemsto be inserted and send the items
    to ES.

    :param es_client: Elasticsearch client
    :param queue str: SQS URL
    :batch_size int: maximum number of messages to be processed, 0 for
                     all messages.
    """

    processed_messages = 0
    for msg in sqs_messages(queue):

        #print(msg['stac_item'])
        create_document_in_index(es_client=es_client,
                                 stac_item=msg['stac_item'])

        # Remove message from queue
        if delete_messages:
            SQS_CLIENT.delete_message(
                QueueUrl=queue,
                ReceiptHandle=msg['ReceiptHandle'])

        processed_messages += 1
        if processed_messages == batch_size:
            break

def bulk_process_insert_queue(es_client, queue: str,
                              batch_size: int = 1,
                              delete_messages: bool = True):
    """
    Read queue with itemsto be inserted and send the items
    to ES.

    :param es_client: Elasticsearch client
    :param queue str: SQS URL
    :batch_size int: maximum number of messages to be processed, 0 for
                     all messages.
    """

    processed_messages = 0
    receipts = list()
    items = list()
    for msg in sqs_messages(queue):

        receipts.append(msg['ReceiptHandle'])
        items.append(msg['stac_item'])

        processed_messages += 1
        if processed_messages == batch_size:
            break

    bulk_create_document_in_index(es_client=es_client,
                                  stac_items=items,
                                  update_if_exists=True)

    # Remove messages from queue
    if delete_messages:
        for receipt in receipts:
            SQS_CLIENT.delete_message(
                QueueUrl=queue,
                ReceiptHandle=receipt)

def es_connect(endpoint: str, port: int,
               use_ssl: bool = True, verify_certs: bool = True,
               http_auth=None, timeout: int = 30):
    """
    Connects to ES endpoint, returns Elasticsarch
    low level client.

    :param endpoint str: Elastic endpoint
    :param port int: endpoint TCP port
    :param timeout int: timeout in seconds
    :return: Elastic low level client
    :rtype: class Elasticsearch
    """

    es_client = Elasticsearch(hosts=[{'host':endpoint,
                                      'port':port}],
                              use_ssl=use_ssl,
                              verify_certs=verify_certs,
                              connection_class=RequestsHttpConnection,
                              http_auth=http_auth,
                              timeout=timeout)
    return es_client


def create_stac_index(es_client, timeout: int = 30):
    """
    Create STAC index.

    :param es_client: Elasticsearch client
    :param timeout int: timeout in seconds
    """
    mapping = '''
{
    "mappings": {
        "_doc": {
            "properties": {
                "geometry": {
                    "type": "geo_shape",
                    "tree": "quadtree",
                    "precision": "100m"
                }
            }
        }
    }
}'''
    es_client.indices.create(index='stac', body=mapping,
                             request_timeout=timeout)

def bulk_create_document_in_index(es_client,
                                  stac_items: list,
                                  update_if_exists: bool = False,
                                  timeout: int = 30):
    """
    Create operation, bulk mode
    """

    # @todo include timeout option
    # @todo use generator instead of building list
    stac_updates = list()

    for item in stac_items:
        dict_item = json.loads(item)
        if not update_if_exists:
            bulk_item = dict()
            bulk_item['_type'] = '_doc'
            bulk_item['_id'] = dict_item['id']
            bulk_item['_op_type'] = 'create'
            bulk_item['_index'] = 'stac'
            bulk_item['_source'] = dict_item
            stac_updates.append(bulk_item)
        else:
            bulk_item = dict()
            bulk_item['_type'] = '_doc'
            bulk_item['_id'] = dict_item['id']
            bulk_item['_op_type'] = 'update'
            bulk_item['_index'] = 'stac'
            bulk_item['doc'] = dict_item
            bulk_item['upsert'] = dict_item
            stac_updates.append(bulk_item)

    success, _ = bulk(es_client, stac_updates)

    return success

def create_document_in_index(es_client,
                             stac_item: str,
                             update_if_exists: bool = False,
                             timeout: int = 30):
    """
    Create document in STAC index

    :param es_client: Elasticsearch client
    :param stac_item: String representing the STAC item
    :param timeout int: timeout in seconds
    """


    if not update_if_exists:
        document = json.loads(stac_item)
        es_client.create(index='stac', id=document['id'],
                         body=document, doc_type='_doc',
                         request_timeout=timeout)
    else:
        document = dict()
        document['doc'] = json.loads(stac_item)
        document['doc_as_upsert'] = True
        es_client.update(index='stac', id=document['doc']['id'],
                         body=document, doc_type='_doc',
                         request_timeout=timeout)

def stac_search(es_client):
    """
    Search STAC items

    :param es_client: Elasticsearch client
    """

    query = {
        "query": {
            "bool": {
                "must": {
                    "range": {"properties.datetime": {"gte": "2014-10-21T20:03:12.963", "lte": "2018-11-24T20:03:12.963"}}
                },
                "filter": {
                    "geo_shape": {
                        "geometry": {
                            "shape": {
                                "type": "envelope",
                                "coordinates" : [[-180.0, -90.0], [180.0, 90.0]]
                            },
                            "relation": "intersects"
                        }
                    }
                }
            }
        }
    }
    res = es_client.search(index='stac',
                           doc_type='_doc',
                           body=query)
    return res

def create_stac_index_handler(event, context): # pylint: disable=unused-argument
    """
    Create STAC elasticsearch index
    """

    auth = BotoAWSRequestsAuth(aws_host=os.environ['ES_ENDPOINT'],
                               aws_region=os.environ['AWS_REGION'],
                               aws_service='es')

    # es_client = Elasticsearch(hosts=[{'host':os.environ['ES_ENDPOINT'],
    #                                'port':int(os.environ['ES_PORT'])}],
    #                          connection_class=RequestsHttpConnection,
    #                          use_ssl=True,
    #                          http_auth=auth)
    # print(es_client.info())

    es_client = es_connect(endpoint=os.environ['ES_ENDPOINT'],
                           port=int(os.environ['ES_PORT']),
                           http_auth=auth)
    #print(es_client.info())
    create_stac_index(es_client)

def create_documents_handler(event,
                             context):  # pylint: disable=unused-argument
    """
    Include document in index
    """

    auth = BotoAWSRequestsAuth(aws_host=os.environ['ES_ENDPOINT'],
                               aws_region=os.environ['AWS_REGION'],
                               aws_service='es')
    es_client = es_connect(endpoint=os.environ['ES_ENDPOINT'],
                           port=int(os.environ['ES_PORT']),
                           http_auth=auth)
    if 'queue' in event:
        # Read STAC items from queue
        #process_insert_queue(es_client=es_client,
        #                     queue=event['queue'],
        #                     delete_messages=False)
        bulk_process_insert_queue(es_client=es_client,
                                  queue=event['queue'],
                                  delete_messages=True,
                                  batch_size=10)
    elif 'Records' in event:
        # Lambda called from SQS trigger
        stac_items = list()
        for record in event['Records']:
            #print(json.dumps(record, indent=2))
            stac_items.append(json.loads(record['body'])['Message'])
        bulk_create_document_in_index(es_client=es_client,
                                      stac_items=stac_items,
                                      update_if_exists=True)

    #print(es_client.info())
    #create_document_in_index(es_client)

def stac_search_endpoint_handler(event,
                                 context):  # pylint: disable=unused-argument
    """
    Lambda entry point
    """

    auth = BotoAWSRequestsAuth(aws_host=os.environ['ES_ENDPOINT'],
                               aws_region=os.environ['AWS_REGION'],
                               aws_service='es')
    es_client = es_connect(endpoint=os.environ['ES_ENDPOINT'],
                           port=int(os.environ['ES_PORT']),
                           http_auth=auth)

    #print(json.dumps(event), indent=2)
    if event['httpMethod'] == 'GET':
        document = dict()
        qsp = event['queryStringParameters']
        document['bbox'] = qsp.get('bbox', '-180,-90,180,90')
        document['time'] = qsp.get('time', 'Nonono')
        document['limit'] = int(qsp.get('limit', '10'))
    else:
        document = json.loads(event['body'])

    retmsg = {
        'statusCode': '200',
        'body': json.dumps(document, indent=2),
        'headers': {
            'Content-Type': 'application/json',
        }
    }

    return retmsg
