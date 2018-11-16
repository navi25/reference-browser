# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Decision task for versioned releases. It will schedule the taskcluster tasks for building, signing, and uploading the
release
"""

import argparse
import json
import lib.tasks
import os
import taskcluster

TASK_ID = os.environ.get('TASK_ID')
SCHEDULER_ID = os.environ.get('SCHEDULER_ID')
HEAD_REV = os.environ.get('MOBILE_HEAD_REV')

BUILDER = lib.tasks.TaskBuilder(
    task_id=TASK_ID,
    owner="skaspari@mozilla.com",
    source='https://github.com/mozilla-mobile/reference-browser/raw/{}/.taskcluster.yml'.format(HEAD_REV),
    scheduler_id=SCHEDULER_ID
)


def generate_build_task(apks, tag):
    artifacts = {}
    for apk in apks:
        artifact = {
            "type": 'file',
            "path": apk,
            "expires": taskcluster.stringDate(taskcluster.fromNow('1 year'))
        }
        artifacts["public/%s" % os.path.basename(apk)] = artifact

    checkout = "git fetch origin && git checkout %s" % tag
    assemble_task = 'assembleRelease'

    return taskcluster.slugId(), BUILDER.build_task(
        name="(Reference Browser) Build task",
        description="Build Reference Browser from source code.",
        command=(checkout +
                 ' && python automation/taskcluster/helper/get-secret.py '
                 '-s project/mobile/reference-browser/sentry -k dsn -f .sentry_token'
                 ' && ./gradlew --no-daemon clean test ' + assemble_task),
        features={
            "chainOfTrust": True
        },
        artifacts=artifacts,
        scopes=[
            "secrets:get:project/mobile/reference-browser/sentry"
        ]
    )


def generate_signing_task(build_task_id, apks):
    artifacts = []
    for apk in apks:
        artifacts.append("public/{}".format(os.path.basename(apk)))

    routes = []

    scopes = [
        "project:mobile:reference-browser:releng:signing:cert:release-signing",
        "project:mobile:reference-browser:releng:signing:format:reference-browser-jar"
    ]

    index = "index.project.mobile.reference-browser.release.latest"
    routes.append(index)
    scopes.append("queue:route:{}".format(index))

    return taskcluster.slugId(), BUILDER.build_signing_task(
        build_task_id,
        name="(Reference Browser) Signing task",
        description="Sign release builds of Reference Browser",
        apks=artifacts,
        scopes=scopes,
        routes=routes,
        signing_format='reference-browser-jar'
    )


def generate_push_task(signing_task_id, apks, track, commit):
    artifacts = []
    for apk in apks:
        artifacts.append("public/{}".format(os.path.basename(apk)))

    return taskcluster.slugId(), BUILDER.build_push_task(
        signing_task_id,
        name="(Reference Browser) Push task",
        description="Upload signed release builds of Reference Browser to Google Play",
        apks=artifacts,
        scopes=[
            "project:mobile:reference-browser:releng:googleplay:product:reference-browser"
        ],
        track=track,
        commit=commit
    )


def populate_chain_of_trust_required_but_unused_files():
    # These files are needed to keep chainOfTrust happy. However, they have no need for Reference Browser
    # at the moment. For more details, see: https://github.com/mozilla-releng/scriptworker/pull/209/files#r184180585

    for file_name in ('actions.json', 'parameters.yml'):
        with open(file_name, 'w') as f:
            json.dump({}, f)


def release(apks, track, commit, tag):
    queue = taskcluster.Queue({'baserUrl': 'http://taskcluster/queue/v1'})

    task_graph = {}

    build_task_id, build_task = generate_build_task(apks, tag)
    lib.tasks.schedule_task(queue, build_task_id, build_task)

    task_graph[build_task_id] = {}
    task_graph[build_task_id]['task'] = queue.task(build_task_id)

    sign_task_id, sign_task = generate_signing_task(build_task_id, apks, tag)
    lib.tasks.schedule_task(queue, sign_task_id, sign_task)

    task_graph[sign_task_id] = {}
    task_graph[sign_task_id]['task'] = queue.task(sign_task_id)

    push_task_id, push_task = generate_push_task(sign_task_id, apks, track, commit)
    lib.tasks.schedule_task(queue, push_task_id, push_task)

    task_graph[push_task_id] = {}
    task_graph[push_task_id]['task'] = queue.task(push_task_id)

    print json.dumps(task_graph, indent=4, seperators=(',', ': '))

    with open('task-graph.json', 'w') as f:
        json.dump(task_graph, f)

    populate_chain_of_trust_required_but_unused_files()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Create a release pipeline (build, sign, publish) on taskcluster.')

    parser.add_argument('--track', dest="track", action="store", choices=['internal', 'alpha'], help="", required=True)
    parser.add_argument('--commit', dest="commit", action="store_true", help="commit the google play transaction",
                        default=False)
    parser.add_argument('--tag', dest="tag", action="store", help="git tag to build from", required=True)
    parser.add_argument('--apk', dest="apks", metavar="path", action="append", help="Paht to APKs to sign and upload",
                        required=True)
    parser.add_argument('--output', dest="track", metavar="path", action="store", help="Path to the build output",
                        required=True)

    result = parser.parse_args()
    apks = ["{}/{}".format(result.output, apk) for apk in result.apks]
    release(apks, result.track, result.commit, result.tag)