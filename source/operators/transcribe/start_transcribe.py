# Copyright 2019 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import os
import boto3
import json
from botocore import config
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all
from MediaInsightsEngineLambdaHelper import MediaInsightsOperationHelper
from MediaInsightsEngineLambdaHelper import MasExecutionError
from MediaInsightsEngineLambdaHelper import OutputHelper

patch_all()

region = os.environ['AWS_REGION']

mie_config = json.loads(os.environ['botoConfig'])
config = config.Config(**mie_config)
transcribe = boto3.client("transcribe", config=config)

# TODO: More advanced exception handling, e.g. using boto clienterrors and narrowing exception scopes


def lambda_handler(event, context):
    print("We got this event:\n", event)
    valid_types = ["mp3", "mp4", "wav", "flac"]
    optional_settings = {}
    operator_object = MediaInsightsOperationHelper(event)
    workflow_id = str(event["WorkflowExecutionId"])
    asset_id = event['AssetId']
    job_id = "transcribe" + "-" + workflow_id

    try:
        if "ProxyEncode" in event["Input"]["Media"]:
            bucket = event["Input"]["Media"]["ProxyEncode"]["S3Bucket"]
            key = event["Input"]["Media"]["ProxyEncode"]["S3Key"]
        elif "Video" in event["Input"]["Media"]:
            bucket = event["Input"]["Media"]["Video"]["S3Bucket"]
            key = event["Input"]["Media"]["Video"]["S3Key"]
        elif "Audio" in event["Input"]["Media"]:
            bucket = event["Input"]["Media"]["Audio"]["S3Bucket"]
            key = event["Input"]["Media"]["Audio"]["S3Key"]
        file_type = key.split('.')[-1]
    except Exception:
        operator_object.update_workflow_status("Error")
        operator_object.add_workflow_metadata(TranscribeError="No valid inputs")
        raise MasExecutionError(operator_object.return_output_object())

    if file_type not in valid_types:
        operator_object.update_workflow_status("Error")
        operator_object.add_workflow_metadata(TranscribeError="Not a valid file type")
        raise MasExecutionError(operator_object.return_output_object())
    try:
        custom_vocab = operator_object.configuration["VocabularyName"]
        optional_settings["VocabularyName"] = custom_vocab
    except KeyError:
        # No custom vocab
        pass
    try:
        language_code = operator_object.configuration["TranscribeLanguage"]
    except KeyError:
        operator_object.update_workflow_status("Error")
        operator_object.add_workflow_metadata(TranscribeError="No language code defined")
        raise MasExecutionError(operator_object.return_output_object())

    media_file = 'https://s3.' + region + '.amazonaws.com/' + bucket + '/' + key

    # If mediainfo data is available then use it to avoid transcribing silent videos.
    if "Mediainfo_num_audio_tracks" in event["Input"]["MetaData"]:
        num_audio_tracks = event["Input"]["MetaData"]["Mediainfo_num_audio_tracks"]
        # Check to see if audio tracks were detected by mediainfo
        if num_audio_tracks == "0":
            # If there is no input audio then we're done.
            operator_object.update_workflow_status("Complete")
            return operator_object.return_output_object()
    try:
        response = transcribe.start_transcription_job(
            TranscriptionJobName=job_id,
            LanguageCode=language_code,
            Media={
                "MediaFileUri": media_file
            },
            MediaFormat=file_type,
            Settings=optional_settings
        )
        print(response)
    except Exception as e:
        operator_object.update_workflow_status("Error")
        operator_object.add_workflow_metadata(transcribe_error=str(e))
        raise MasExecutionError(operator_object.return_output_object())
    else:
        if response["TranscriptionJob"]["TranscriptionJobStatus"] == "IN_PROGRESS":
            operator_object.update_workflow_status("Executing")
            operator_object.add_workflow_metadata(TranscribeJobId=job_id, AssetId=asset_id, WorkflowExecutionId=workflow_id)
            return operator_object.return_output_object()
        elif response["TranscriptionJob"]["TranscriptionJobStatus"] == "FAILED":
            operator_object.update_workflow_status("Error")
            operator_object.add_workflow_metadata(TranscribeJobId=job_id, TranscribeError=str(response["TranscriptionJob"]["FailureReason"]))
            raise MasExecutionError(operator_object.return_output_object())
        elif response["TranscriptionJob"]["TranscriptionJobStatus"] == "COMPLETE":
            operator_object.update_workflow_status("Executing")
            operator_object.add_workflow_metadata(TranscribeJobId=job_id, AssetId=asset_id, WorkflowExecutionId=workflow_id)
            return operator_object.return_output_object()
        else:
            operator_object.update_workflow_status("Error")
            operator_object.add_workflow_metadata(TranscribeJobId=job_id,
                                          TranscribeError="Unhandled error for this job: {job_id}".format(job_id=job_id))
            raise MasExecutionError(operator_object.return_output_object())
