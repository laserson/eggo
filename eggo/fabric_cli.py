# Licensed to Big Data Genomics (BDG) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The BDG licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import json
from cStringIO import StringIO

from fabric.api import (
    task, env, execute, local, open_shell, put, cd, run, prefix, shell_env,
    require, hosts, path)
from fabric.contrib.files import append

import eggo.director
from eggo.util import build_dest_filename
from eggo.config import eggo_config, generate_luigi_cfg


# user that fabric connects as
env.user = eggo_config.get('spark_ec2', 'user')
# ensure fabric uses EC2 private key when connecting
if not env.key_filename:
    env.key_filename = eggo_config.get('aws', 'ec2_private_key_file')


@task
def provision():
    provision_cmd = ('{spark_home}/ec2/spark-ec2 -k {ec2_key_pair} '
                     '-i {ec2_private_key_file} -s {slaves} -t {type_} '
                     '-r {region} {zone_arg} '
                     '--copy-aws-credentials launch {stack_name}')
    az = eggo_config.get('spark_ec2', 'availability_zone')
    zone_arg = '--zone {0}'.format(az) if az != '' else ''
    interp_cmd = provision_cmd.format(
        spark_home=eggo_config.get('client_env', 'spark_home'),
        ec2_key_pair=eggo_config.get('aws', 'ec2_key_pair'),
        ec2_private_key_file=eggo_config.get('aws', 'ec2_private_key_file'),
        slaves=eggo_config.get('spark_ec2', 'num_slaves'),
        type_=eggo_config.get('spark_ec2', 'instance_type'),
        region=eggo_config.get('spark_ec2', 'region'),
        zone_arg=zone_arg,
        stack_name=eggo_config.get('spark_ec2', 'stack_name'))
    local(interp_cmd)

    # tag all the provisioned instances
    from boto.ec2 import connect_to_region
    exec_ctx = eggo_config.get('execution', 'context')
    conn = connect_to_region(eggo_config.get(exec_ctx, 'region'))
    instances = conn.get_only_instances(
        filters={'key-name': [eggo_config.get('aws', 'ec2_key_pair')]})
    for instance in instances:
        instance.add_tag('stack_name', eggo_config.get(exec_ctx, 'stack_name'))


def get_master_host():
    getmaster_cmd = ('{spark_home}/ec2/spark-ec2 -k {ec2_key_pair} '
                     '-i {ec2_private_key_file} get-master {stack_name}')
    interp_cmd = getmaster_cmd.format(
        spark_home=eggo_config.get('client_env', 'spark_home'),
        ec2_key_pair=eggo_config.get('aws', 'ec2_key_pair'),
        ec2_private_key_file=eggo_config.get('aws', 'ec2_private_key_file'),
        stack_name=eggo_config.get('spark_ec2', 'stack_name'))
    result = local(interp_cmd, capture=True)
    host = result.split('\n')[2].strip()
    return host


def get_slave_hosts():
    def do():
        with prefix('source /root/spark-ec2/ec2-variables.sh'):
            result = run('echo $SLAVES').split()
        return result
    master = get_master_host()
    return execute(do, hosts=master)[master]


def get_worker_hosts():
    return [get_master_host()] + get_slave_hosts()


@task
def deploy_config():
    work_path = eggo_config.get('worker_env', 'work_path')
    eggo_config_path = eggo_config.get('worker_env', 'eggo_config_path')
    luigi_config_path = eggo_config.get('worker_env', 'luigi_config_path')

    def do():
        # 0. ensure that the work path exists on the worker nodes
        run('mkdir -p {work_path}'.format(work_path=work_path))

        # 1. copy local eggo config file to remote cluster
        put(local_path=os.environ['EGGO_CONFIG'],
            remote_path=eggo_config_path)

        # 2. deploy the luigi config file
        buf = StringIO(generate_luigi_cfg())
        put(local_path=buf,
            remote_path=luigi_config_path)

    execute(do, hosts=get_worker_hosts())


def install_pip():
    with cd('/tmp'):
        run('curl -O https://bootstrap.pypa.io/get-pip.py')
        run('python get-pip.py')


def install_fabric_luigi():
    with cd('/tmp'):
        # protobuf for luigi
        run('yum install -y protobuf protobuf-devel protobuf-python')
        run('pip install mechanize')
        run('pip install fabric')
        run('pip install ordereddict')  # for py2.6 compat (for luigi)
        run('pip install luigi')


def install_maven(version):
    run('mkdir -p /usr/local/apache-maven')
    with cd('/usr/local/apache-maven'):
        run('wget http://apache.mesi.com.ar/maven/maven-3/{version}/binaries/'
            'apache-maven-{version}-bin.tar.gz'.format(version=version))
        run('tar -xzf apache-maven-{version}-bin.tar.gz'.format(
            version=version))
    env_mod = [
        'export M2_HOME=/usr/local/apache-maven/apache-maven-{version}'.format(
            version=version),
        'export M2=$M2_HOME/bin',
        'export PATH=$PATH:$M2']
    append('~/.bash_profile', env_mod)
    run('mvn -version')


def install_adam(path, fork, branch):
    with cd(path):
        run('git clone https://github.com/bigdatagenomics/adam.git')
        with cd('adam'):
            # check out desired fork/branch
            if fork != 'bigdatagenomics':
                run('git pull --no-commit https://github.com/{fork}/adam.git'
                    ' {branch}'.format(fork=fork, branch=branch))
            elif branch != 'master':
                run('git checkout origin/{branch}'.format(branch=branch))
            # build adam
            with shell_env(MAVEN_OPTS='-Xmx1024m -XX:MaxPermSize=512m'):
                run('mvn clean package -DskipTests')


def install_eggo(path, fork, branch):
    with cd(path):
        run('git clone https://github.com/bigdatagenomics/eggo.git')
        with cd('eggo'):
            # check out desired fork/branch
            if fork != 'bigdatagenomics':
                run('git pull --no-commit https://github.com/{fork}/eggo.git'
                    ' {branch}'.format(fork=fork, branch=branch))
            elif branch != 'master':
                run('git checkout origin/{branch}'.format(branch=branch))
            run('python setup.py install')


@task
def setup_master():
    work_path = eggo_config.get('worker_env', 'work_path')
    adam_fork = eggo_config.get('versions', 'adam_fork')
    adam_branch = eggo_config.get('versions', 'adam_branch')
    eggo_fork = eggo_config.get('versions', 'eggo_fork')
    eggo_branch = eggo_config.get('versions', 'eggo_branch')

    def do():
        run('mkdir -p {work_path}'.format(work_path=work_path))
        install_pip()
        install_fabric_luigi()
        install_maven(eggo_config.get('versions', 'maven'))
        install_adam(work_path, adam_fork, adam_branch)
        install_eggo(work_path, eggo_fork, eggo_branch)
        # restart Hadoop
        run('/root/ephemeral-hdfs/bin/stop-all.sh')
        run('/root/ephemeral-hdfs/bin/start-all.sh')

    execute(do, hosts=get_master_host())


@task
def setup_slaves():
    work_path = eggo_config.get('worker_env', 'work_path')
    eggo_fork = eggo_config.get('versions', 'eggo_fork')
    eggo_branch = eggo_config.get('versions', 'eggo_branch')

    def do():
        env.parallel = True
        install_pip()
        install_eggo(work_path, eggo_fork, eggo_branch)

    execute(do, hosts=get_slave_hosts())


@task
def login():
    execute(open_shell, hosts=get_master_host())


@task
def teardown():
    teardown_cmd = ('{spark_home}/ec2/spark-ec2 -k {ec2_key_pair} '
                    '-i {ec2_private_key_file} destroy {stack_name}')
    interp_cmd = teardown_cmd.format(
        spark_home=eggo_config.get('client_env', 'spark_home'),
        ec2_key_pair=eggo_config.get('aws', 'ec2_key_pair'),
        ec2_private_key_file=eggo_config.get('aws', 'ec2_private_key_file'),
        stack_name=eggo_config.get('spark_ec2', 'stack_name'))
    local(interp_cmd)


@task
def toast(config):
    def do():
        with open(config, 'r') as ip:
            config_data = json.load(ip)
        dag_class = config_data['dag']
        # push the toast config to the remote machine
        toast_config_worker_path = os.path.join(
            eggo_config.get('worker_env', 'work_path'),
            build_dest_filename(config))
        put(local_path=config,
            remote_path=toast_config_worker_path)
        # TODO: run on central scheduler instead
        toast_cmd = ('toaster.py --local-scheduler {clazz} '
                     '--ToastConfig-config {toast_config}'.format(
                        clazz=dag_class,
                        toast_config=toast_config_worker_path))
        
        hadoop_bin = os.path.join(eggo_config.get('worker_env', 'hadoop_home'), 'bin')
        toast_env = {'EGGO_CONFIG': eggo_config.get('worker_env', 'eggo_config_path'),  # bc toaster.py imports eggo_config which must be init on the worker
                     'LUIGI_CONFIG_PATH': eggo_config.get('worker_env', 'luigi_config_path'),
                     'AWS_ACCESS_KEY_ID': eggo_config.get('aws', 'aws_access_key_id'),  # bc dataset dnload pushes data to S3 TODO: should only be added if the dfs is S3
                     'AWS_SECRET_ACCESS_KEY': eggo_config.get('aws', 'aws_secret_access_key'),  # TODO: should only be added if the dfs is S3
                     'SPARK_HOME': eggo_config.get('worker_env', 'spark_home')}
        with path(hadoop_bin):
            with shell_env(**toast_env):
                run(toast_cmd)
    
    execute(do, hosts=get_master_host())


@task
def update_eggo():
    work_path = eggo_config.get('worker_env', 'work_path')
    eggo_fork = eggo_config.get('versions', 'eggo_fork')
    eggo_branch = eggo_config.get('versions', 'eggo_branch')

    def do():
        env.parallel = True
        run('rm -rf $EGGO_HOME')
        install_eggo(work_path, eggo_fork, eggo_branch)

    execute(do, hosts=[get_master_host()] + get_slave_hosts())


# Director commands (experimental)

@task
def provision_director():
    env.user = 'ec2-user' # use ec2-user for launcher instance login
    eggo.director.provision()


@task
def list_director():
    eggo.director.list()


@task
def login_director():
    env.user = 'ec2-user' # use ec2-user for gateway instance login
    eggo.director.login()


@task
def cm_web_proxy():
    eggo.director.cm_web_proxy()


@task
def hue_web_proxy():
    eggo.director.hue_web_proxy()


@task
def yarn_web_proxy():
    eggo.director.yarn_web_proxy()


@task
def teardown_director():
    env.user = 'ec2-user' # use ec2-user for launcher instance login
    eggo.director.teardown()
