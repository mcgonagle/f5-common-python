# coding=utf-8
#
# Copyright 2015-2016 F5 Networks Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# NOTE:  Code taken from Effective Python Item 26


from distutils.version import LooseVersion
import logging

from f5.sdk_exception import F5SDKError
from f5.sdk_exception import UnsupportedMethod


class InvalidCommand(F5SDKError):
    """Raise this if command argument supplied is invalid."""
    pass


class UnsupportedTmosVersion(F5SDKError):
    """Raise the error if a class of an API is instantiated,

    on a TMOS version where API was not yet implemented/supported.
    """
    pass


class LazyAttributesRequired(F5SDKError):
    """Raised when a object accesses a lazy attribute that is not listed"""
    pass


class ToDictMixin(object):
    """Convert an object's attributes to a dictionary"""
    traversed = {}
    Containers = tuple, list, set, frozenset, dict

    def to_dict(self):
        ToDictMixin.traversed = {}
        return self._to_dict()

    def _to_dict(self):
        result = self._traverse_dict(self.__dict__)
        return result

    def _traverse_dict(self, instance_dict):
        output = {}
        for key, value in instance_dict.items():
            output[key] = self._traverse(key, value)
        return output

    def _traverse(self, key, value):
        if isinstance(value, ToDictMixin.Containers) or\
           hasattr(value, '__dict__'):
            if id(value) in ToDictMixin.traversed:
                return ToDictMixin.traversed[id(value)]
            else:
                ToDictMixin.traversed[id(value)] = ['TraversalRecord', key]
        if isinstance(value, ToDictMixin):
            return value._to_dict()
        elif isinstance(value, dict):
            return self._traverse_dict(value)
        elif isinstance(value, list):
            return [self._traverse(key, item) for item in value]
        elif hasattr(value, '__dict__'):
            return self._traverse_dict(value.__dict__)
        else:
            return value


class LazyAttributeMixin(object):
    """Allow attributes to be created lazily based on the allowed values"""
    def __getattr__(container, name):
        # ensure this object supports lazy attrs.
        cls_name = container.__class__.__name__
        if 'allowed_lazy_attributes' not in container._meta_data:
            error_message = ('"allowed_lazy_attributes" not in',
                             'container._meta_data for class %s' % cls_name)
            raise LazyAttributesRequired(error_message)

        # ensure the requested attr is present
        lower_attr_names =\
            [la.__name__.lower() for la in
                container._meta_data['allowed_lazy_attributes']]
        if name not in lower_attr_names:
            error_message = "'%s' object has no attribute '%s'"\
                % (container.__class__, name)
            raise AttributeError(error_message)

        # Instantiate and potentially set the attr on the object
        # Issue #112 -- Only call setattr here if the lazy attribute
        # is NOT a `Resource`.  This should allow for only 1 ltm attribute
        # but many nat attributes just like the BIGIP device.
        for lazy_attribute in container._meta_data['allowed_lazy_attributes']:
            if name == lazy_attribute.__name__.lower():
                attribute = lazy_attribute(container)
                bases = [base.__name__ for base in lazy_attribute.__bases__]
                # Doing version check per each resource
                container._check_supported_versions(attribute)
                if 'Resource' not in bases:
                    setattr(container, name, attribute)
                return attribute

    def _check_supported_versions(container, attribute):
        tmos_v = container._meta_data['bigip'].tmos_version
        minimum = attribute._meta_data['minimum_version']
        if LooseVersion(tmos_v) < LooseVersion(minimum):
            error = "There was an attempt to access API which " \
                    "has not been implemented or supported " \
                    "in the device's TMOS version: {}. "\
                    "Minimum TMOS version supported is {}".format(
                        tmos_v, minimum)
            raise UnsupportedTmosVersion(error)


class ExclusiveAttributesMixin(object):
    """Overrides ``__setattr__`` to remove exclusive attrs from the object."""
    def __setattr__(self, key, value):
        '''Remove any of the existing exclusive attrs from the object

        Objects attributes can be exclusive for example disable/enable.  So
        we need to make sure objects only have one of these attributes at
        at time so that the updates won't fail.
        '''
        if '_meta_data' in self.__dict__:
            # Sometimes this is called prior to full object construction
            for attr_set in self._meta_data['exclusive_attributes']:
                if key in attr_set:
                    new_set = set(attr_set) - set([key])
                    [self.__dict__.pop(n, '') for n in new_set]
        # Now set the attribute
        super(ExclusiveAttributesMixin, self).__setattr__(key, value)


class CommandExecutionMixin(object):
    """This adds command execution option on the objects.

    These objects do not support create, delete, load, and require
    a separate method of execution. Commands do not have
    direct mapping to an HTTP method so usage of POST
    and an absolute URI is required.

    """

    def create(self, **kwargs):
        '''Create is not supported for command execution

        :raises: UnsupportedOperation
        '''
        raise UnsupportedMethod(
            "%s does not support the create method" % self.__class__.__name__
        )

    def delete(self, **kwargs):
        '''Delete is not supported for command execution

        :raises: UnsupportedOperation
        '''
        raise UnsupportedMethod(
            "%s does not support the delete method" % self.__class__.__name__
        )

    def load(self, **kwargs):
        '''Load is not supported for command execution

                :raises: UnsupportedOperation
        '''
        raise UnsupportedMethod(
            "%s does not support the load method" % self.__class__.__name__
        )

    def exec_cmd(self, command, **kwargs):

        cmds = self._meta_data['allowed_commands']

        if command not in self._meta_data['allowed_commands']:
            error_message = "The command value {0} does not exist" \
                            "Valid commands are {1}".format(command, cmds)
            raise InvalidCommand(error_message)

        return self._exec_cmd(command, **kwargs)

    def _exec_cmd(self, command, **kwargs):
        '''Create a new method as command has specific requirements.

        There is a handful of the TMSH global commands supported,
        so this method requires them as a parameter.

        :raises: InvalidCommand
        '''

        kwargs['command'] = command
        self._check_exclusive_parameters(**kwargs)
        requests_params = self._handle_requests_params(kwargs)
        self._check_command_parameters(**kwargs)
        session = self._meta_data['bigip']._meta_data['icr_session']
        response = session.post(
            self._meta_data['uri'], json=kwargs, **requests_params)
        self._local_update(response.json())

        return self


class FileUploadMixin(object):
    def _upload_file(self, filepathname, **kwargs):
        with open(filepathname, 'rb') as fileobj:
            self._upload(fileobj, **kwargs)

    def _upload(self, fileinterface, **kwargs):
        size = len(fileinterface.read())
        fileinterface.seek(0)
        requests_params = self._handle_requests_params(kwargs)
        session = self._meta_data['icr_session']
        chunk_size = kwargs.pop('chunk_size', 512 * 1024)
        start = 0
        while True:
            file_slice = fileinterface.read(chunk_size)
            if not file_slice:
                break

            current_bytes = len(file_slice)
            if current_bytes < chunk_size:
                end = size
            else:
                end = start + current_bytes
            headers = {
                'Content-Range': '%s-%s/%s' % (start,
                                               end - 1,
                                               size),
                'Content-Type': 'application/octet-stream'}
            data = {'data': file_slice,
                    'headers': headers,
                    'verify': False}
            logging.debug(data)
            requests_params.update(data)
            session.post(self.file_bound_uri,
                         **requests_params)
            start += current_bytes


class DeviceMixin(object):
    '''Manage BigIP device cluster in a general way.'''

    def get_device_info(self, bigip):
        '''Get device information about a specific BigIP device.

        :param bigip: bigip object --- device to inspect
        :returns: bigip object
        '''

        coll = bigip.tm.cm.devices.get_collection()
        device = [device for device in coll if device.selfDevice == 'true']
        assert len(device) == 1
        return device[0]
