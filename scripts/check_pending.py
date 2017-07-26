#! /usr/bin/env python

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import sys
import argparse
import urllib
import urllib2
import json
import StringIO
import gzip

__version__ = "1.4"

BB_PENDING_URL = 'https://secure.pub.build.mozilla.org/builddata/buildjson/builds-pending.js'
ALLTHETHINGS_URL = 'https://secure.pub.build.mozilla.org/builddata/reports/allthethings.json.gz'
QUEUE_PENDING_URL = "https://queue.taskcluster.net/v1/pending/{}/{}"
TC_WORKERS_URL = 'https://hg.mozilla.org/integration/mozilla-inbound/raw-file/tip/taskcluster/taskgraph/util/workertypes.py'
status_code = {'OK': 0, 'WARNING': 1, "CRITICAL": 2, "UNKNOWN": 3}


###################################
# Get Buildbot pending builds&tests
###################################

def get_allthethings_json():
    response = urllib2.urlopen(ALLTHETHINGS_URL, timeout=30)
    compressed_data = StringIO.StringIO(response.read())
    decompressed_data = gzip.GzipFile(fileobj=compressed_data)
    return json.load(decompressed_data)


# get the pending jobs and their count
def get_pending_counts():
    pending_counts = {}
    response = urllib2.urlopen(BB_PENDING_URL, timeout=30)
    result = json.loads(response.read())
    for branch in result['pending'].keys():
        for revision in result['pending'][branch].keys():
            for request in result['pending'][branch][revision]:
                k = request['buildername']
                if k in pending_counts.keys():
                    pending_counts[k] += 1
                else:
                    pending_counts[k] = 1
    return pending_counts


# get the builder names and their corresponding slavepools
def get_builders_and_slavepools(allthethings_json):
    builders_and_slavepools = []
    slavepool_cache = {}
    for builder_name in allthethings_json['builders'].keys():
        slavepool_id = allthethings_json['builders'][builder_name]['slavepool']
        pools = []
        if slavepool_id in slavepool_cache:
            pools = slavepool_cache[slavepool_id]
        else:
            slavepool = allthethings_json['slavepools'][slavepool_id]
            for s in slavepool:
                if s[:s.rfind('-')] not in pools:
                    pools.append(s[:s.rfind('-')])
            slavepool_cache[slavepool_id] = pools
        if not pools:
            builders_and_slavepools.append([builder_name, 'None'])
        else:
            for i in range(0, len(pools)):
                pools[i] = str(pools[i])
            builders_and_slavepools.append([builder_name, pools])
    return builders_and_slavepools


# get the slavepools
def get_slavepools(builders_and_slavepools):
    slavepools = []
    for j in range(0, len(builders_and_slavepools)):
        duplicate = 0
        for k in range (0,j):
            if builders_and_slavepools[j][1] == builders_and_slavepools[k][1]:
                duplicate = 1
                break
        if duplicate == 0:
            slavepools.append([builders_and_slavepools[j][1]])
    return slavepools


# compute the number of pending builds by slavepool and split them in builds&tests
def get_count_by_slavepool(pending_counts, builders_and_slavepools, slavepools):
    count_by_slavepool = []
    pending_builds = []
    pending_tests = []

    for m in range(0, len(slavepools)):
        count_by_slavepool.append([slavepools[m][0], 0])
    for i,j in pending_counts.iteritems():
        for k in range(0, len(builders_and_slavepools)):
            if i == builders_and_slavepools[k][0]:
                for m in range(0, len(count_by_slavepool)):
                    if builders_and_slavepools[k][1] == count_by_slavepool[m][0]:
                        count_by_slavepool[m][1] += j

    for i in range(0, len(count_by_slavepool)):
        if count_by_slavepool[i][0][0].startswith(('b-2008', 'y-2008', 'bld', 'av')):
            pending_builds.append(count_by_slavepool[i])
        else:
            pending_tests.append(count_by_slavepool[i])
    return pending_builds, pending_tests


###################################
# Get TaskCluster pending tasks
###################################

def get_tc_workers(url):
    tc_workers = {
        'builders': {},
        'testers': {}
    }
    response = urllib.URLopener()
    response.retrieve(TC_WORKERS_URL, "./worker_types.py")
    from worker_types import WORKER_TYPES

    for k in tc_workers.keys():
        for key, value in WORKER_TYPES.iteritems():
            provisioner = key.split('/')[0]
            worker = key.split('/')[1]
            if provisioner not in ['aws-provisioner-v1', 'releng-hardware']:
                continue

            if worker.find('-b-') > 0 and k == 'builders':
                if provisioner not in tc_workers[k]:
                    tc_workers[k][provisioner] = [worker]
                else:
                    tc_workers[k][provisioner].append(worker)

            if worker.find('-t-') > 0 and k == 'testers':
                if provisioner not in tc_workers[k]:
                    tc_workers[k][provisioner] = [worker]
                else:
                    tc_workers[k][provisioner].append(worker)
    return tc_workers


def get_pending_count_per_worker(provisioner, worker_type):
    pending_url = QUEUE_PENDING_URL.format(provisioner, worker_type)
    response = urllib2.urlopen(pending_url, timeout=30)
    result = json.loads(response.read())
    return result['pendingTasks']


def get_pending_count_per_worker_type(tc_workers):
    pending_builds = {}
    pending_tests = {}

    for k in tc_workers.keys():
        for provisioner in tc_workers[k].keys():
            for worker in tc_workers[k][provisioner]:
                if k == 'builders':
                    pending_builds[worker] = get_pending_count_per_worker(provisioner, worker)
                if k == 'testers':
                    pending_tests[worker] = get_pending_count_per_worker(provisioner, worker)
    return pending_builds, pending_tests

# compute total sum of pending builds and tests (buildbot + taskcluster) and sort it
def get_pending_list(bb, tc):
    total = bb[:]
    for k,v in tc.iteritems():
        total.append([k, v])
    total = sorted(total, key=lambda list: list[1], reverse=True)
    return total


# get the status based on the specified thresholds
def pending_status(pending, critical_threshold, warning_threshold):
    if pending >= critical_threshold:
        return 'CRITICAL'
    elif pending >= warning_threshold:
        return 'WARNING'
    else:
        return 'OK'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-B', '--builds', action='store_true', dest='builds',
                        help='compute number of pending builds per machine pool')
    parser.add_argument('-t', '--tests', action='store_true', dest='tests',
                        help='compute number of pending tests per machine pool')
    parser.add_argument(
        '-C', '--builds_critical', action='store', type=int, dest='builds_critical_threshold',
        default=300, metavar="CRITICAL", help='Set builds CRITICAL level as integer eg. 300')
    parser.add_argument(
        '-W', '--builds_warning', action='store', type=int, dest='builds_warning_threshold',
        default=200, metavar="WARNING", help='Set builds WARNING level as integer eg. 200')
    parser.add_argument(
        '-c', '--tests_critical', action='store', type=int, dest='tests_critical_threshold',
        default=3000, metavar="CRITICAL", help='Set tests CRITICAL level as integer eg. 3000')
    parser.add_argument(
        '-w', '--tests_warning', action='store', type=int, dest='tests_warning_threshold',
        default=2000, metavar="WARNING", help='Set tests WARNING level as integer eg. 2000')
    parser.add_argument('-b', '--buildbot', action='store_true', dest='buildbot',
                        help='Display pending jobs on buildbot machine pools')
    parser.add_argument('-T', '--taskcluster', action='store_true', dest='taskcluster',
                        help='Display pending jobs on taskcluster workers')

    args = parser.parse_args()

    try:
        allthethings_json = get_allthethings_json()
        pending_counts = get_pending_counts()
        builders_and_slavepools = get_builders_and_slavepools(allthethings_json)
        slavepools = get_slavepools(builders_and_slavepools)
        bb_pending_builds, bb_pending_tests = get_count_by_slavepool(pending_counts, builders_and_slavepools, slavepools)
        tc_workers = get_tc_workers(TC_WORKERS_URL)
        tc_pending_builds, tc_pending_tests = get_pending_count_per_worker_type(tc_workers)

        pending_builds = get_pending_list(bb_pending_builds, tc_pending_builds)
        pending_tests = get_pending_list(bb_pending_tests, tc_pending_tests)
        builds_status = pending_status(pending_builds[0][1], args.builds_critical_threshold, args.builds_warning_threshold)
        tests_status = pending_status(pending_tests[0][1], args.tests_critical_threshold, args.tests_warning_threshold)

        if args.buildbot:
            bb_pending = bb_pending_builds + bb_pending_tests
            for i in range(0, len(bb_pending)):
                print bb_pending[i][0], ":", bb_pending[i][1]

        if args.taskcluster:
            tc_pending = dict(tc_pending_builds.items() + tc_pending_tests.items())
            for k, v in tc_pending.iteritems():
                print k, ':', v

        if args.builds:
            output = '%s Pending builds: %i' % (builds_status, pending_builds[0][1])
            if pending_builds[0][1] > 0 :
                output += " on %s" % pending_builds[0][0]
                print output
                sys.exit(status_code[builds_status])

        if args.tests:
            output = '%s Pending tests: %i' % (tests_status, pending_tests[0][1])
            if pending_tests[0][1] > 0 :
                output += " on %s" % pending_tests[0][0]
                print output
                sys.exit(status_code[tests_status])
    except Exception as e:
        print e
        sys.exit(status_code.get('UNKNOWN'))

