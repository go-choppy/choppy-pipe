#!/usr/bin/env python
"""
Choppy is a tool for submitting workflows via command-line to the cromwell execution engine servers. For more
info, visit https://github.com/broadinstitute/choppy/blob/master/README.md.
"""
import argparse
import sys
import os
import csv
import shutil
import logging
import getpass
import json
import zipfile
import pprint
import time
import pytz
import datetime
from . import config as c
from .utils import parse_samples, render_app, write, kv_list_to_dict, submit, install_app
from .single_bucket import print_log_exit
from .cromwell import Cromwell
from .monitor import Monitor
from .validator import Validator
from .single_bucket import SingleBucket

__author__ = "Amr Abouelleil, Paul Cao"
__copyright__ = "Copyright 2017, The Broad Institute"
__credits__ = ["Amr Abouelleil", "Paul Cao", "Jean Chang"]
__license__ = "GPL"
__version__ = "1.7.0"
__maintainer__ = "Amr Abouelleil, Paul Cao"
__email__ = "amr@broadinstitute.org"
__status__ = "Production"

# Logging setup
logger = logging.getLogger('choppy')
logger.setLevel(logging.DEBUG)
# create file handler which logs even debug messages
logfile = os.path.join(c.log_dir, '{}.{}.choppy.log'.format(getpass.getuser(), str(time.strftime("%m.%d.%Y"))))
fh = logging.FileHandler(logfile)
fh.setLevel(logging.DEBUG)
# create console handler with a higher log level
ch = logging.StreamHandler()
ch.setLevel(logging.ERROR)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
ch.setFormatter(formatter)
# add the handlers to the logger
logger.addHandler(fh)
logger.addHandler(ch)


def check_dir(path):
    if not os.path.isdir(path):
        os.makedirs(path)
    else:
        raise Exception("%s is not empty" % path)


def is_valid(path):
    """
    Integrates with ArgParse to validate a file path.
    :param path: Path to a file.
    :return: The path if it exists, otherwise raises an error.
    """
    if not os.path.exists(path):
        raise argparse.ArgumentTypeError(("{} is not a valid file path.\n".format(path)))
    else:
        return path


def is_valid_zip(path):
    """
    Integrates with argparse to validate a file path and verify that the file is a zip file.
    :param path: Path to a file.
    :return: The path if it exists and is a zip file, otherwise raises an error.
    """
    is_valid(path)
    if not zipfile.is_zipfile(path):
        e = "{} is not a valid zip file.\n".format(path)
        logger.error(e)
        raise argparse.ArgumentTypeError(e)
    else:
        return path


def call_run(args):
    """
    Optionally validates inputs and starts a workflow on the Cromwell execution engine if validation passes. Validator
    returns an empty list if valid, otherwise, a list of errors discovered.
    :param args: run subparser arguments.
    :return: JSON response with Cromwell workflow ID.
    """
    if args.validate:
        call_validate(args)

    #prep labels and add user
    labels_dict = kv_list_to_dict(args.label) if kv_list_to_dict(args.label) != None else {}
    labels_dict['username'] = args.username
    cromwell = Cromwell(host=args.server)
    result = cromwell.jstart_workflow(wdl_file=args.wdl, json_file=args.json, dependencies=args.dependencies,
                                      disable_caching=args.disable_caching,
                                      extra_options=kv_list_to_dict(args.extra_options), bucket=args.bucket,
                                      custom_labels=labels_dict)

    print("-------------Cromwell Links-------------")
    links = get_cromwell_links(args.server, result['id'], cromwell.port)
    print(links['metadata'])
    print(links['timing'])
    logger.info("Metadata:{}".format(links['metadata']))
    logger.info("Timing Graph:{}".format(links['timing']))

    args.workflow_id = result['id']

    if args.monitor:
        # this sleep is to allow job to get started in Cromwell before labeling or monitoring.
        # Probably better ways to do this but for now this works.
        time.sleep(5)

        print ("These will also be e-mailed to you when the workflow completes.")
        retry = 4
        while retry != 0:
            try:
                call_monitor(args)
                retry = 0
            except KeyError as e:
                logger.debug(e)
                retry = retry - 1
    return result


def call_query(args):
    """
    Get various types of data on a particular workflow ID.
    :param args:  query subparser arguments.
    :return: A list of json responses based on queries selected by the user.
    """
    cromwell = Cromwell(host=args.server)
    responses = []
    if args.workflow_id == None or args.workflow_id == "None" and not args.label:
        return call_list(args)
    if args.label:
        logger.info("Label query requested.")
        labeled = cromwell.query_labels(labels=kv_list_to_dict(args.label))
        return labeled
    if args.status:
        logger.info("Status requested.")
        status = cromwell.query_status(args.workflow_id)
        responses.append(status)
    if args.metadata:
        logger.info("Metadata requested.")
        metadata = cromwell.query_metadata(args.workflow_id)
        responses.append(metadata)
    if args.logs:
        logger.info("Logs requested.")
        logs = cromwell.query_logs(args.workflow_id)
        responses.append(logs)
    logger.debug("Query Results:\n" + str(responses))
    return responses


def call_validate(args):
    """
    Calls the Validator to validate input json. Exits with feedback to user regarding errors in json or reports no
    errors found.
    :param args: validation subparser arguments.
    :return:
    """
    logger.info("Validation requested.")
    validator = Validator(wdl=args.wdl, json=args.json)
    result = validator.validate_json()
    if len(result) != 0:
        e = "{} input file contains the following errors:\n{}".format(args.json, "\n".join(result))
        # This will also print to stdout so no need for a print statement
        logger.critical(e)
        sys.exit(1)
    else:
        s = 'No errors found in {}'.format(args.wdl)
        print(s)
        logger.info(s)


def call_abort(args):
    """
    Abort a workflow with a given workflow id.
    :param args: abort subparser args.
    :return: JSON containing abort response.
    """
    cromwell = Cromwell(host=args.server)
    logger.info("Abort requested")
    return cromwell.stop_workflow(workflow_id=args.workflow_id)


def call_monitor(args):
    """
    Calls Monitoring to report to user the status of their workflow at regular intervals.
    :param args: 'monitor' subparser arguments.
    :return:
    """
    logger.info("Monitoring requested")

    print("-------------Monitoring Workflow-------------")
    try:
        if args.daemon:
            m = Monitor(host=args.server, user="*", no_notify=args.no_notify, verbose=args.verbose,
                        interval=args.interval)
            m.run()
        else:
            m = Monitor(host=args.server, user=args.username, no_notify=args.no_notify, verbose=args.verbose,
                        interval=args.interval)
            if args.workflow_id:
                m.monitor_workflow(args.workflow_id)
            else:
                m.monitor_user_workflows()
    except Exception as e:
        print_log_exit(msg=str(e), sys_exit=False, ple_logger=logger)


def call_restart(args):
    """
    Call cromwell restart to restart a failed workflow.
    :param args: restart subparser arguments.
    :return:
    """
    logger.info("Restart requested")
    cromwell = Cromwell(host=args.server)
    result = cromwell.restart_workflow(workflow_id=args.workflow_id, disable_caching=args.disable_caching)

    if result is not None and "id" in result:
        msg = "Workflow restarted successfully; new workflow-id: " + str(result['id'])
        print(msg)
        logger.info(msg)
    else:
        msg = "Workflow was not restarted successfully; server response: " + str(result)
        print(msg)
        logger.critical(msg)


def get_cromwell_links(server, workflow_id, port):
    """
    Get metadata and timing graph URLs.
    :param server: cromwell host
    :param workflow_id: UUID for workflow
    :param port: port for cromwell server of interest
    :return: Dictionary containing useful links
    """
    return {'metadata': 'http://{}:{}/api/workflows/v1/{}/metadata'.format(server, port, workflow_id),
            'timing': 'http://{}:{}/api/workflows/v1/{}/timing'.format(server, port, workflow_id)}


def call_explain(args):
    logger.info("Explain requested")
    cromwell = Cromwell(host=args.server)
    (result, additional_res, stdout_res) = cromwell.explain_workflow(workflow_id=args.workflow_id,
                                                                     include_inputs=args.input)

    def my_safe_repr(object, context, maxlevels, level):
        typ = pprint._type(object)
        if typ is unicode:
            object = str(object)
        return pprint._safe_repr(object, context, maxlevels, level)

    printer = pprint.PrettyPrinter()
    printer.format = my_safe_repr
    if result is not None:
        print("-------------Workflow Status-------------")
        printer.pprint(result)

        if len(additional_res) > 0:
            print("-------------Additional Parameters-------------")
            printer.pprint(additional_res)

        if len(stdout_res) > 0:
            for log in stdout_res["failed_jobs"]:
                print("-------------Failed Stdout-------------")
                print ("Shard: "+ log["stdout"]["label"])
                print (log["stdout"]["name"] + ":")
                print (log["stdout"]["log"])
                print ("-------------Failed Stderr-------------")
                print ("Shard: " + log["stderr"]["label"])
                print (log["stderr"]["name"] + ":")
                print (log["stderr"]["log"])

        print("-------------Cromwell Links-------------")
        links = get_cromwell_links(args.server, result['id'], cromwell.port)
        print (links['metadata'])
        print (links['timing'])

    else:
        print("Workflow not found.")

    args.monitor = True
    return None


def call_list(args):
    username = "*" if args.all else args.username
    m = Monitor(host=args.server, user=username, no_notify=True, verbose=True,
                interval=None)

    def get_iso_date(dt):
        tz = pytz.timezone("US/Eastern")
        return tz.localize(dt).isoformat()

    def process_job(job):
        links = get_cromwell_links(args.server, job['id'], m.cromwell.port)
        job['metadata'] = links['metadata']
        job['timing'] = links['timing']
        return job

    def my_safe_repr(object, context, maxlevels, level):
        typ = pprint._type(object)
        if typ is unicode:
            object = str(object)
        return pprint._safe_repr(object, context, maxlevels, level)

    start_date_str = get_iso_date(datetime.datetime.now() - datetime.timedelta(days=int(args.days)))
    q = m.get_user_workflows(raw=True, start_time=start_date_str)
    try:
        result = q["results"]
        if args.filter:
            result = [res for res in result if res['status'] in args.filter]
        result = map(lambda j: process_job(j), result)
        printer = pprint.PrettyPrinter()
        printer.format = my_safe_repr
        printer.pprint(result)
        args.monitor = True
        return result
    except KeyError as e:
        logger.critical('KeyError: Unable to find key {}'.format(e))


def call_label(args):
    """
    Apply labels to a workflow that currently exists in the database.
    :param args: label subparser arguments
    :return:
    """
    cromwell = Cromwell(host=args.server)
    labels_dict = kv_list_to_dict(args.label)
    response = cromwell.label_workflow(workflow_id=args.workflow_id, labels=labels_dict)
    if response.status_code == 200:
        print("Labels successfully applied:\n{}".format(response.content))
    else:
        logger.critical("Unable to apply specified labels:\n{}".format(response.content))


def call_log(args):
    """
    Get workflow logs via cromwell API.
    :param args: log subparser arguments.
    :return:
    """
    cromwell = Cromwell(host=args.server)
    res = cromwell.get('logs', args.workflow_id)
    print res["calls"]

    command = ""

    # for each task, extract the command used
    for key in res["calls"]:
        stderr = res["calls"][key][0]["stderr"]
        script = "/".join(stderr.split("/")[:-1]) + "/script"

        with open(script, 'r') as f:
            command_log = f.read()

        command = command + key + ":\n\n"
        command = command + command_log + "\n\n"

    print(command)  # print to stdout
    return None


def call_email(args):
    """
    MVP pass-through function for testing desirability of a call_email feature. If users want a full-fledged function
    we can rework this.
    :param args: email subparser args.
    :return:
    """
    args.verbose = False
    args.no_notify = False
    args.interval = 0
    call_monitor(args)


def call_upload(args):
    """
    :param args:
    :return:
    """
    created_files = list()
    if args.dependencies:
        path = os.path.dirname(args.dependencies)
        zip_ref = zipfile.ZipFile(args.dependencies, 'r')
        zip_files = zip_ref.namelist()
        for fn in zip_files:
            f = os.path.join(path, fn)
            if not os.path.exists(f):
                zip_ref.extract(fn, path)
                created_files.append(f)
        zip_ref.close()
    b = SingleBucket(args.bucket)
    uploaded_files = b.upload_workflow_input_files(args.wdl, args.json)
    for f in created_files:
        os.unlink(f)
    print('The following files have been uploaded to {}:\n{}'.format(args.bucket, '\n'.join(uploaded_files)))


def call_list_apps(args):
    if os.path.isdir(c.app_dir):
        files = os.listdir(c.app_dir)
        print(files)
    else:
        raise Exception("choppy.conf.general.app_dir is wrong.")


def call_batch(args):
    project_name = args.project_name
    app_name = args.app_name
    samples = args.samples
    label = args.label

    working_dir = os.getcwd()
    project_path = os.path.join(working_dir, project_name)
    check_dir(project_path)

    samples_data = parse_samples(samples)
    successed_samples = []

    try:
        for sample in samples_data:
            if 'sample_id' not in sample.keys():
                raise Exception("Your samples file must contain sample_id column.")
            else:
                # make project_name/sample_id directory
                sample_path = os.path.join(project_path, sample.get('sample_id'))
                check_dir(sample_path)
                app_dir = os.path.join(c.app_dir, app_name)

                sample['project_name'] = project_name

                inputs = render_app(app_dir, 'inputs', sample)
                write(sample_path, 'inputs', inputs)

                wdl = render_app(app_dir, 'workflow.wdl', sample)
                write(sample_path, 'workflow.wdl', wdl)

                src_dependencies = os.path.join(app_dir, 'tasks.zip')
                dest_dependencies = os.path.join(sample_path, 'tasks.zip')
                shutil.copyfile(src_dependencies, dest_dependencies)

                # result = submit(wdl, inputs, dest_dependencies, label, username=args.username,
                #                 server=args.server)

                # links = get_cromwell_links(args.server, result['id'], result.port)

                # sample['metadata_link'] = links['metadata']
                # sample['timing_link'] = links['timing']
                # sample['workflow_id'] = result['id']
                successed_samples.append(sample)
    finally:
        keys = successed_samples[0].keys()
        with open(os.path.join(project_path, 'submitted.csv'), 'wb') as fsuccess:
            dict_writer = csv.DictWriter(fsuccess, keys)
            dict_writer.writeheader()
            dict_writer.writerows(successed_samples)                


def call_testapp(args):
    project_name = args.project_name
    samples = args.samples

    working_dir = os.getcwd()
    project_path = os.path.join(working_dir, project_name)
    check_dir(project_path)

    samples_data = parse_samples(samples)
    successed_samples = []

    try:
        for sample in samples_data:
            if 'sample_id' not in sample.keys():
                raise Exception("Your samples file must contain sample_id column.")
            else:
                # make project_name/sample_id directory
                sample_path = os.path.join(project_path, sample.get('sample_id'))
                check_dir(sample_path)
                app_dir = args.app_dir

                sample['project_name'] = project_name

                inputs = render_app(app_dir, 'inputs', sample)
                write(sample_path, 'inputs', inputs)

                wdl = render_app(app_dir, 'workflow.wdl', sample)
                write(sample_path, 'workflow.wdl', wdl)

                src_dependencies = os.path.join(app_dir, 'tasks.zip')
                dest_dependencies = os.path.join(sample_path, 'tasks.zip')
                shutil.copyfile(src_dependencies, dest_dependencies)

                # result = submit(wdl, inputs, dest_dependencies, label, username=args.username,
                #                 server=args.server)

                # links = get_cromwell_links(args.server, result['id'], result.port)

                # sample['metadata_link'] = links['metadata']
                # sample['timing_link'] = links['timing']
                # sample['workflow_id'] = result['id']
                successed_samples.append(sample)
    finally:
        keys = successed_samples[0].keys()
        with open(os.path.join(project_path, 'submitted.csv'), 'wb') as fsuccess:
            dict_writer = csv.DictWriter(fsuccess, keys)
            dict_writer.writeheader()
            dict_writer.writerows(successed_samples)


def call_installapp(args):
    app_zip_file = args.zip_file
    install_app(c.app_dir, app_zip_file)


parser = argparse.ArgumentParser(
    description='Description: A tool for executing and monitoring WDLs to Cromwell instances.',
    usage='choppy <positional argument> [<args>]',
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)

sub = parser.add_subparsers()
restart = sub.add_parser(name='restart',
                         description='Restart a submitted workflow.',
                         usage='choppy restart <workflow id>',
                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)
restart.add_argument('workflow_id', action='store', help='workflow id of workflow to restart.')
restart.add_argument('-S', '--server', action='store', required=True, type=str, choices=c.servers,
                     help='Choose a cromwell server from {}'.format(c.servers))
restart.add_argument('-M', '--monitor', action='store_true', default=True, help=argparse.SUPPRESS)
restart.add_argument('-D', '--disable_caching', action='store_true', default=False, help="Don't used cached data.")
restart.set_defaults(func=call_restart)

explain = sub.add_parser(name='explain',
                         description='Explain the status of a workflow.',
                         usage='choppy explain <workflowid>',
                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)
explain.add_argument('workflow_id', action='store', help='workflow id of workflow to abort.')
explain.add_argument('-S', '--server', action='store', required=True, type=str, choices=c.servers,
                     help='Choose a cromwell server from {}'.format(c.servers))
explain.add_argument('-I', '--input', action='store_true', default=False, help=argparse.SUPPRESS)
explain.add_argument('-M', '--monitor', action='store_false', default=False, help=argparse.SUPPRESS)
explain.set_defaults(func=call_explain)

log = sub.add_parser(name='log',
                     description='Print the commands used in a workflow.',
                     usage='choppy log <workflowid>',
                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
log.add_argument('workflow_id', action='store', help='workflow id of workflow to print commands for.')
log.add_argument('-S', '--server', action='store', required=True, type=str, choices=c.servers,
                 help='Choose a cromwell server from {}'.format(c.servers))
log.add_argument('-M', '--monitor', action='store_false', default=False, help=argparse.SUPPRESS)
log.set_defaults(func=call_log)

abort = sub.add_parser(name='abort',
                       description='Abort a submitted workflow.',
                       usage='choppy abort <workflow id>',
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
abort.add_argument('workflow_id', action='store', help='workflow id of workflow to abort.')
abort.add_argument('-S', '--server', action='store', required=True, type=str, choices=c.servers,
                   help='Choose a cromwell server from {}'.format(c.servers))
abort.add_argument('-M', '--monitor', action='store_false', default=False, help=argparse.SUPPRESS)

abort.set_defaults(func=call_abort)

monitor = sub.add_parser(name='monitor',
                         description='Monitor a particular workflow and notify user via e-mail upon completion. If a'
                                     'workflow ID is not provided, user-level monitoring is assumed.',
                         usage='choppy monitor <workflow_id> [<args>]',
                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)
monitor.add_argument('workflow_id', action='store', nargs='?',
                     help='workflow id for workflow to monitor. Do not specify if user-level monitoring is desired.')
monitor.add_argument('-u', '--username', action='store', default=getpass.getuser(),
                     help='Owner of workflows to monitor.')
monitor.add_argument('-i', '--interval', action='store', default=30, type=int,
                     help='Amount of time in seconds to elapse between status checks.')
monitor.add_argument('-V', '--verbose', action='store_true', default=False,
                     help='When selected, choppy will write the current status to STDOUT until completion.')
monitor.add_argument('-n', '--no_notify', action='store_true', default=False,
                     help='When selected, disable choppy e-mail notification of workflow completion.')
monitor.add_argument('-S', '--server', action='store', required=True, type=str, choices=c.servers,
                     help='Choose a cromwell server from {}'.format(c.servers))
monitor.add_argument('-M', '--monitor', action='store_true', default=True, help=argparse.SUPPRESS)
monitor.add_argument('-D', '--daemon', action='store_true', default=False,
                     help="Specify if this is a daemon for all users.")
monitor.set_defaults(func=call_monitor)

query = sub.add_parser(name='query',
                       description='Query cromwell for information on the submitted workflow.',
                       usage='choppy query <workflow id> [<args>]',
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
query.add_argument('workflow_id', nargs='?', default="None", help='workflow id for workflow execution of interest.')
query.add_argument('-s', '--status', action='store_true', default=False, help='Print status for workflow to stdout')
query.add_argument('-m', '--metadata', action='store_true', default=False, help='Print metadata for workflow to stdout')
query.add_argument('-l', '--logs', action='store_true', default=False, help='Print logs for workflow to stdout')
query.add_argument('-u', '--username', action='store', default=getpass.getuser(), help='Owner of workflows to query.')
query.add_argument('-L', '--label', action='append', help='Query status of all workflows with specific label(s).')
query.add_argument('-d', '--days', action='store', default=7, help='Last n days to query.')
query.add_argument('-S', '--server', action='store', required=True, type=str, choices=c.servers,
                   help='Choose a cromwell server from {}'.format(c.servers))
query.add_argument('-f', '--filter', action='append', type=str, choices=c.run_states + c.terminal_states,
                   help='Filter by a workflow status from those listed above. May be specified more than once.')
query.add_argument('-a', '--all', action='store_true', default=False, help='Query for all users.')
query.add_argument('-M', '--monitor', action='store_false', default=False, help=argparse.SUPPRESS)

query.set_defaults(func=call_query)

run = sub.add_parser(name='run',
                     description='Submit a WDL & JSON for execution on a Cromwell VM.',
                     usage='choppy run <wdl file> <json file> [<args>]',
                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)

run.add_argument('wdl', action='store', type=is_valid, help='Path to the WDL to be executed.')
run.add_argument('json', action='store', type=is_valid, help='Path the json inputs file.')
run.add_argument('-v', '--validate', action='store_true', default=False,
                 help='Validate WDL inputs in json file.')
run.add_argument('-l', '--label', action='append', help='A key:value pair to assign. May be used multiple times.')
run.add_argument('-m', '--monitor', action='store_true', default=False,
                 help='Monitor the workflow and receive an e-mail notification when it terminates.')
run.add_argument('-i', '--interval', action='store', default=30, type=int,
                 help='If --monitor is selected, the amount of time in seconds to elapse between status checks.')
run.add_argument('-o', '--extra_options', action='append',
                 help='Additional workflow options to pass to Cromwell. Specify as k:v pairs. May be specified multiple'
                      + 'times for multiple options. See https://github.com/broadinstitute/cromwell#workflow-options' +
                      'for available options.')
run.add_argument('-V', '--verbose', action='store_true', default=False,
                 help='If selected, choppy will write the current status to STDOUT until completion while monitoring.')
run.add_argument('-n', '--no_notify', action='store_true', default=False,
                 help='When selected, disable choppy e-mail notification of workflow completion.')
run.add_argument('-d', '--dependencies', action='store', default=None, type=is_valid_zip,
                 help='A zip file containing one or more WDL files that the main WDL imports.')
run.add_argument('-b', '--bucket', action='store', default=c.default_bucket,
                 help='Name of bucket where files were uploaded. Default is {}'.format(c.default_bucket))
run.add_argument('-D', '--disable_caching', action='store_true', default=False, help="Don't used cached data.")
run.add_argument('-S', '--server', action='store', required=True, type=str, choices=c.servers,
                 help='Choose a cromwell server from {}'.format(c.servers))
run.add_argument('-u', '--username', action='store', default=getpass.getuser(), help=argparse.SUPPRESS)
run.add_argument('-w', '--workflow_id', help=argparse.SUPPRESS)
run.add_argument('-x', '--daemon', action='store_true', default=False, help=argparse.SUPPRESS)
run.set_defaults(func=call_run)

validate = sub.add_parser(name='validate',
                          description='Validate (but do not run) a json for a specific WDL file.',
                          usage='choppy validate <wdl_file> <json_file>',
                          formatter_class=argparse.ArgumentDefaultsHelpFormatter)
validate.add_argument('wdl', action='store', type=is_valid, help='Path to the WDL associated with the json file.')
validate.add_argument('json', action='store', type=is_valid, help='Path the json inputs file to validate.')
validate.add_argument('-M', '--monitor', action='store_false', default=False, help=argparse.SUPPRESS)
validate.set_defaults(func=call_validate)

label = sub.add_parser(name='label',
                       description='Label a specific workflow with one or more key/value pairs.',
                       usage='choppy label <workflow_id> [<args>]',
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
label.add_argument('workflow_id', nargs='?', default="None", help='workflow id for workflow to label.')
label.add_argument('-S', '--server', action='store', required=True, type=str, choices=c.servers,
                   help='Choose a cromwell server from {}'.format(c.servers))
label.add_argument('-l', '--label', action='append', help='A key:value pair to assign. May be used multiple times.')
label.add_argument('-M', '--monitor', action='store_false', default=False, help=argparse.SUPPRESS)
label.set_defaults(func=call_label)

email = sub.add_parser(name ='email',
                       description='Email data to user regarding a workflow.',
                       usage='choppy label <workflow_id> [<args>]',
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
email.add_argument('workflow_id', nargs='?', default="None", help='workflow id for workflow to label.')
email.add_argument('-S', '--server', action='store', required=True, type=str, choices=c.servers,
                   help='Choose a cromwell server from {}'.format(c.servers))
email.add_argument('-u', '--username', action='store', default=getpass.getuser(), help='username of user to e-mail to')
email.add_argument('-M', '--monitor', action='store_false', default=False, help=argparse.SUPPRESS)
email.add_argument('-D', '--daemon', action='store_true', default=False,
                   help=argparse.SUPPRESS)
email.set_defaults(func=call_email)

upload = sub.add_parser(name='upload',
                        description='Upload files required for workflow execution to Cloud storage.',
                        usage='choppy upload <wdl> <json>',
                        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
upload.add_argument('wdl', action='store', type=is_valid, help='Path to the WDL associated with the json file.')
upload.add_argument('json', action='store', type=is_valid, help='Path the json inputs file to validate.')
upload.add_argument('-b', '--bucket', action='store', default=c.default_bucket,
                    help='Name of destination bucket for upload. Default is {}'.format(c.default_bucket))
upload.add_argument('-d', '--dependencies', action='store', default=None, type=is_valid_zip,
                    help='A zip file containing one or more WDL files that the main WDL imports.')
upload.set_defaults(func=call_upload)

batch = sub.add_parser(name="batch",
                       description="Submit batch jobs for execution on a Cromwell VM.",
                       usage="choppy batch <app_name> <samples>",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
batch.add_argument('app_name', action='store', help='The app name for your project.')
batch.add_argument('samples', action='store', type=is_valid, help='Path the samples file to validate.')
batch.add_argument('--project-name', action='store', required=True, help='Your project name.')
batch.add_argument('-l', '--label', action='append', help='A key:value pair to assign. May be used multiple times.')
batch.add_argument('-S', '--server', action='store', default='localhost', type=str, help='Choose a cromwell server.')
batch.add_argument('-u', '--username', action='store', default=getpass.getuser(), help=argparse.SUPPRESS)
batch.set_defaults(func=call_batch)

testapp = sub.add_parser(name="testapp",
                       description="Test an app.",
                       usage="choppy testapp <app_dir> <samples>",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
testapp.add_argument('app_dir', action='store', type=is_valid, help='The app path for your project.')
testapp.add_argument('samples', action='store', type=is_valid, help='Path the samples file to validate.')
testapp.add_argument('--project-name', action='store', required=True, help='Your project name.')
testapp.set_defaults(func=call_testapp)

installapp = sub.add_parser(name="install",
                       description="Install an app.",
                       usage="choppy install <zip_file>",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
installapp.add_argument('zip_file', action='store', type=is_valid_zip, help='The app zip file.')
installapp.set_defaults(func=call_installapp)

wdllist = sub.add_parser(name="apps",
                         description="List all apps that is supported by choppy.",
                         usage="choppy apps",
                         formatter_class=argparse.ArgumentDefaultsHelpFormatter)
wdllist.set_defaults(func=call_list_apps)

def main():
    args = parser.parse_args()
    # Get user's username so we can tag workflows and logs for them.
    user = getpass.getuser()
    try:
        if args.server == "cloud":
            args.server = c.cloud_server
        if args.server == "gscid-cloud":
            args.server = c.gscid_cloud_server
    except AttributeError:
        pass
    logger.info("\n-------------New Choppy Execution by {}-------------".format(user))
    logger.info("Parameters chosen: {}".format(vars(args)))
    result = args.func(args)
    logger.info("Result: {}".format(result))
    # If we aren't using persistent monitoring, we'll give the user a basically formated json dump to stdout.
    try:
        if not args.monitor:
            print(json.dumps(result, indent=4))
    except AttributeError:
        pass
    logger.info("\n-------------End Choppy Execution by {}-------------".format(user))


if __name__ == "__main__":
    sys.exit(main())

