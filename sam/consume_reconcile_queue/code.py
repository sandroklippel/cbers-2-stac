"""consume_reconcile_queue"""

import os
import re
import json
import boto3

SQS_CLIENT = boto3.client('sqs')
S3_CLIENT = boto3.client('s3')

def populate_queue_with_quicklooks(bucket, prefix, suffix, queue):
    """
    Populate queue with items to be processed. The items are obtained
    from bucket/prefix/*/suffix
    """
    suffix = r'.*' + suffix
    files = S3_CLIENT.list_objects_v2(Bucket=bucket,
                                      Prefix=prefix,
                                      RequestPayer='requester')

    while True:
        for file in files['Contents']:
            if re.search(suffix, file['Key']):
                #print(file['Key'])
                message = dict()
                message['Message'] = json.dumps({'Records':[{'s3':{
                    'object':{
                        'key':file['Key'],
                        'reconcile':1}}}]})
                SQS_CLIENT.send_message(QueueUrl=queue,
                                        MessageBody=json.dumps(message))
        if not files['IsTruncated']:
            break
        files = S3_CLIENT.\
                list_objects_v2(Bucket=bucket,
                                Prefix=prefix,
                                ContinuationToken=\
                                files['NextContinuationToken'],
                                RequestPayer='requester')

def handler(event, context): # pylint: disable=unused-argument
    """Lambda entry point
    Event keys:
      prefix(string): Common prefix of S3 keys to be sent to queue
    """

    if 'queue' in event:
        # Consume payload data from job queue, one job only
        response = SQS_CLIENT.receive_message(QueueUrl=event['queue'])
        receipt_handle = response['Messages'][0]['ReceiptHandle']
        populate_queue_with_quicklooks(bucket=os.\
                                       environ['CBERS_PDS_BUCKET'],
                                       prefix=response['Messages'][0]['Body'],
                                       suffix='.jpg',
                                       queue=os.environ['NEW_SCENES_QUEUE'])
        #r_params.append(json.loads(response['Messages'][0]['Body']))
        #print(json.dumps(response, indent=2))
        SQS_CLIENT.delete_message(QueueUrl=event['queue'],
                                  ReceiptHandle=receipt_handle)

    else:
        # Lambda called from SQS trigger
        for record in event['Records']:
            populate_queue_with_quicklooks(bucket=os.\
                                           environ['CBERS_PDS_BUCKET'],
                                           prefix=record['body'],
                                           suffix='.jpg',
                                           queue=os.environ['NEW_SCENES_QUEUE'])
