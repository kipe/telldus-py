# Copyright (c) 2012 Erik Johansson <erik@ejohansson.se>
# 
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307
# USA

try:
    import queue
except ImportError:
    import Queue as queue

import ctypes
import threading

import telldus.library

ByRefArgType = type(ctypes.byref(ctypes.c_int(0)))

class MockLibLoader(object):
    def __init__(self, mocklib):
        object.__init__(self)
        self.load_count = 0
        self.mocklib = mocklib

    def LoadLibrary(self, name):
        self.load_count += 1
        return self.mocklib

class MockTelldusCoreLib(object):
    def __getattr__(self, name):
        if name in telldus.library.Library._functions:
            func = MockCFunction(name, self)
            object.__setattr__(self, name, func)
            return func
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name in telldus.library.Library._functions:
            func = getattr(self, name)
            func.implementation = value
        else:
            object.__setattr__(self, name, value)

class MockCFunction(object):
    def __init__(self, name, lib):
        object.__init__(self)
        self.name = name
        self.lib = lib
        self.implementation = None
        self.restype = None
        self.argtypes = None
        self.errcheck = None

    def __call__(self, *args):
        if self.implementation is None:
            raise NotImplementedError("%s is not implemented" % self.name)

        if self.argtypes is None:
            raise NotImplementedError("%s not configured" % self.name)

        if len(self.argtypes) != len(args):
            raise TypeError("%s() takes exactly %d argument(s) (%d given)" %
                            (self.name, len(self.argument), len(args)))

        # Verify that the arguments are of correct type
        for c_type, value in zip(self.argtypes, args):
            if type(value) is not c_type:
                # The 'raw' attribute is the pointer for string buffers
                if hasattr(value, 'raw'):
                    c_value = c_type(value.raw)
                elif type(value) is not ByRefArgType:
                    c_value = c_type(value)

        res = self.implementation(*args)

        if self.errcheck is not None:
            res = self.errcheck(res, self, args)
        return res

class MockEventDispatcher(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self, name="MockEventDispatcher")

        self.next_callback_id = 1

        # callback id -> callback object
        self.callbacks = {}

        # Lists of callback ids
        self.device_callbacks = []
        self.device_change_callbacks = []
        self.raw_device_callbacks = []
        self.sensor_callbacks = []
        self.controller_callbacks = []

        self.queue = queue.Queue()
        self.running = True
        self.start()

    def setup_lib_functions(self, mocklib):
        def tdUnregisterCallback(id_):
            del self.callbacks[id_]
            return 1
        mocklib.tdUnregisterCallback = tdUnregisterCallback

        def register_callback(active_list, callback, context):
            callback_id = self.next_callback_id
            self.next_callback_id += 1

            self.callbacks[callback_id] = (callback, context)
            active_list.append(callback_id)
            return callback_id

        callback_types = {
            "tdRegisterDeviceEvent": self.device_callbacks,
            "tdRegisterDeviceChangeEvent": self.device_change_callbacks,
            "tdRegisterRawDeviceEvent": self.raw_device_callbacks,
            "tdRegisterSensorEvent": self.sensor_callbacks,
            "tdRegisterControllerEvent": self.controller_callbacks
        }

        for name, id_list in callback_types.items():
            func = lambda callback, context, id_list=id_list: register_callback(
                id_list, callback, context)
            setattr(mocklib, name, func)

    def run(self):
        while self.running:
            (callback, args) = self.queue.get()
            try:
                callback(*args)
            except:
                pass
            finally:
                self.queue.task_done()

    def stop(self):
        assert self.running
        def stop_thread():
            self.running = False
        self.queue_event(stop_thread)
        self.queue.join()

    def queue_event(self, callback, *args):
        assert self.running
        self.queue.put((callback, args))

    def _trigger_event(self, callback_list, *args):
        for callback_id in callback_list:
            if callback_id in self.callbacks:
                (callback, context) = self.callbacks[callback_id]
                event_args = args + (callback_id, context)
                self.queue_event(callback, *event_args)
        # Make sure all events are delivered
        self.queue.join()

    def trigger_device_event(self, device_id, method, data):
        self._trigger_event(self.device_callbacks, device_id, method, data)

    def trigger_device_change_event(self, device_id, event, type_):
        self._trigger_event(self.device_change_callbacks, device_id,
                            event, type_)

    def trigger_raw_device_event(self, data, controller_id):
        self._trigger_event(self.raw_device_callbacks, data, controller_id)

    def trigger_sensor_event(self, protocol, model, id_, dataType, value,
                             timestamp):
        self._trigger_event(self.sensor_callbacks, protocol, model, id_,
                            dataType, value, timestamp)

    def trigger_controller_event(self, controller_id, event, type_, new_value):
        self._trigger_event(self.controller_callbacks, controller_id, event,
                            type_, new_value)
