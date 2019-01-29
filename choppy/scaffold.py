# -*- coding:utf-8 -*-
from __future__ import unicode_literals
import os
from jinja2 import Environment, FileSystemLoader
import choppy.config as c
from choppy.app_utils import copy_and_overwrite


class NoSuchDirectory(Exception):
    pass


class NoSuchFile(Exception):
    pass


class Scaffold:
    def __init__(self, output_dir='.'):
        file_list = ['README.md', 'workflow.wdl', 'inputs', 'defaults']
        dir_list = ['tasks', 'test', 'docker']
        self.scaffold_dir = os.path.join(c.resource_dir, 'scaffold_template')
        self.file_list = [os.path.join(self.scaffold_dir, file) for file in file_list]
        self.dir_list = [os.path.join(self.scaffold_dir, dir) for dir in dir_list]

        self._check_file(self.file_list)
        self._check_dir(self.dir_list)

        # Template Env
        self.env = Environment(loader=FileSystemLoader(self.scaffold_dir))
        self.output_dir = output_dir

    def _check_file(self, file_list):
        for file in file_list:
            if not os.path.isfile(file):
                raise NoSuchFile('No such file(%s) in scaffold.' % file)
            else:
                continue

    def _check_dir(self, dir_list):
        for dir in dir_list:
            if not os.path.isdir(dir):
                raise NoSuchDirectory('No such directory(%s) in scaffold.' % dir)
            else:
                continue

    def _gen_readme(self, output_file='README.md', **kwargs):
        template = self.env.get_template('README.md')
        rendered_tmpl = template.render(**kwargs)

        if output_file:
            with open(output_file, 'w') as f:
                f.write(rendered_tmpl)
                return output_file
        else:
            return rendered_tmpl

    def _gen_defaults(self, output_file='defaults', **kwargs):
        template = self.env.get_template('defaults')
        rendered_tmpl = template.render(**kwargs)

        if output_file:
            with open(output_file, 'w') as f:
                f.write(rendered_tmpl)
                return output_file
        else:
            return rendered_tmpl

    def _gen_inputs(self, output_file='inputs', **kwargs):
        template = self.env.get_template('inputs')
        rendered_tmpl = template.render(**kwargs)

        if output_file:
            with open(output_file, 'w') as f:
                f.write(rendered_tmpl)
                return output_file
        else:
            return rendered_tmpl

    def _gen_workflow(self, output_file='workflow.wdl', **kwargs):
        template = self.env.get_template('workflow.wdl')
        rendered_tmpl = template.render(**kwargs)

        if output_file:
            with open(output_file, 'w') as f:
                f.write(rendered_tmpl)
                return output_file
        else:
            return rendered_tmpl

    def _copy_tasks(self):
        tasks_dir = os.path.join(self.scaffold_dir, 'tasks')
        dest_dir = os.path.join(self.output_dir, 'tasks')
        copy_and_overwrite(tasks_dir, dest_dir)

    def _copy_docker(self):
        docker_dir = os.path.join(self.scaffold_dir, 'docker')
        dest_dir = os.path.join(self.output_dir, 'docker')
        copy_and_overwrite(docker_dir, dest_dir)

    def _copy_test(self):
        test_dir = os.path.join(self.scaffold_dir, 'test')
        dest_dir = os.path.join(self.output_dir, 'test')
        copy_and_overwrite(test_dir, dest_dir)

    def generate(self):
        self._copy_docker()
        self._copy_tasks()
        self._copy_test()
        readme = os.path.join(self.output_dir, 'README.md')
        self._gen_readme(output_file=readme)

        defaults = os.path.join(self.output_dir, 'defaults')
        self._gen_defaults(output_file=defaults)

        inputs = os.path.join(self.output_dir, 'inputs')
        self._gen_inputs(output_file=inputs)

        workflow = os.path.join(self.output_dir, 'workflow.wdl')
        self._gen_workflow(workflow)