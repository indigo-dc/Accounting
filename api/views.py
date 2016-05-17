import datetime
import logging
import MySQLdb
import os

from dirq.queue import Queue, QueueError
from rest_framework.pagination import PaginationSerializer
from django.conf import settings
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from rest_framework.response import Response
from rest_framework.views import APIView


class CloudSummaryRecordView(APIView):
    """
    Submit Cloud Accounting Records or Retrieve Cloud Accounting Summaries.
    GET Useage:

    .../computing/summaryrecord?Group=<group_name>&from=<date_from>&to=<date_to>

    Will return the summary for group_name at all services,
    between date_from and date_to as daily summaries

    .../computing/summaryrecord?service=<service_name>&from=<date_from>&to=<date_to>

    Will return the summary for service_name at all groups,
    between date_from and date_to as daily summaries

    .../computing/summaryrecord?&from=<date_from>
    Will give summary for whole infrastructure from <data> to now

    POST Usage:
    .../computing/summaryrecord

    Will save Cloud Accounting Records for later loading.
    """

    def get(self, request, format=None):
        """
        Submit Cloud Accounting Records.

        .../computing/summaryrecord?Group=<group_name>&from=<date_from>&to=<date_to>

        Will return the summary for group_name at all services,
        between date_from and date_to as daily summaries

        .../computing/summaryrecord?service=<service_name>&from=<date_from>&to=<date_to>

        Will return the summary for service_name at all groups,
        between date_from and date_to as daily summaries

        .../computing/summaryrecord?&from=<date_from>
        Will give summary for whole infrastructure from <data> to now
        """
        logger = logging.getLogger(__name__)

        # parse query parameters
        group_name = request.GET.get('group', '')
        if group_name is "":
            group_name = None
        service_name = request.GET.get('service', '')

        if service_name is "":
            service_name = None

        start_date = request.GET.get('from', '')
        if start_date is "":
            # querying without a from is not supported
            return Response(status=501)

        end_date = request.GET.get('to', '')
        if end_date is "":
            end_date = datetime.datetime.now()

        logger.info("%s %s %s %s",
                    group_name,
                    service_name,
                    start_date,
                    end_date)

        # get the data requested
        database = MySQLdb.connect('localhost', 'root', '', 'apel_rest')
        cursor = database.cursor()

        if group_name is not None:
            cursor.execute('select * from CloudSummaries where VOGroupID = %s and EarliestStartTime > %s and LatestStartTime < %s',
                           [group_name, start_date, end_date])

        elif service_name is not None:
            cursor.execute('select * from CloudSummaries where SiteID = %s and EarliestStartTime > %s and LatestStartTime < %s',
                           [service_name, start_date, end_date])

        else:
            cursor.execute('select * from CloudSummaries where EarliestStartTime > %s',
                           [start_date])

        return_headers = ["VOGroupID",
                          "SiteID",
                          "UpdateTime",
                          "WallDuration",
                          "EarliestStartTime",
                          "LatestStartTime"]

        columns = cursor.description
        results = []
        for value in cursor.fetchall():
            result = {}
            for index, column in enumerate(value):
                header = columns[index][0]
                if header in return_headers:
                    result.update({header: column})
            results.append(result)

        page = request.GET.get('page')
        paginator = Paginator(results, 100)  # 100 results per page

        try:
            result = paginator.page(page)
        except PageNotAnInteger:
            # If page is not an integer, deliver first page.
            result = paginator.page(1)
        except EmptyPage:
            # If page is out of range (e.g. 9999),
            # deliver last page of results.
            result = paginator.page(paginator.num_pages)

        # context allows for clickable REST Framework links
        serializer = PaginationSerializer(instance=result,
                                          context={'request': request})
        result = serializer.data

        return Response(result, status=200)

    def post(self, request, format=None):
        """
        Retrieve Cloud Accounting Summaries.

        .../computing/summaryrecord

        Will save Cloud Accounting Records for later loading.
        """
        logger = logging.getLogger(__name__)

        try:
            empaid = request.META['HTTP_EMPA_ID']
        except KeyError:
            empaid = 'noid'

        logger.info("Received message. ID = %s", empaid)

        try:
            signer = request.META['SSL_CLIENT_S_DN']
        except KeyError:
            signer = "None"

        if "_content" in request.POST.dict():
            # then POST likely to come via the rest api framework
            # hence use the content of request.POST as message
            body = request.POST.get('_content')

        else:
            # POST likely to comes through a browser client or curl
            # hence use request.body as message
            body = request.body

        logger.debug("Message body received: %s", body)

        for header in request.META:
            logger.debug("%s: %s", header, request.META[header])

        # taken from ssm2
        QSCHEMA = {'body': 'string',
                   'signer': 'string',
                   'empaid': 'string?'}

        inqpath = os.path.join(settings.QPATH, 'incoming')
        inq = Queue(inqpath, schema=QSCHEMA)

        try:
            name = inq.add({'body': body,
                            'signer': signer,
                            'empaid': empaid})
        except QueueError as err:
            logger.error("Could not save %s/%s: %s", inqpath, name, err)

            response = "Data could not be saved to disk, please try again."
            return Response(response, status=500)

        logger.info("Message saved to in queue as %s/%s", inqpath, name)

        response = "Data successfully loaded."
        return Response(response, status=202)
