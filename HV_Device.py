__author__ = 'testbeam'

# ============================
# IMPORTS
# ============================
import interfaces
from interfaces import *
from interfaces.Keithley24XX import Keithley24XX
from interfaces.Keithley23X import Keithley23X
from interfaces.Keithley6517B import Keithley6517B
from interfaces.Keithley2657 import Keithley2657
from threading import Thread
from ConfigParser import ConfigParser, NoOptionError
from time import time, sleep, strftime
from math import copysign
import sys
import logging
import os

# for *_interface do import bla

# U = Keithley24XX("blub")


# ============================
# MAIN CLASS
# ============================
class HVDevice(Thread):
    def __init__(self, config, device_no, hot_start):
        Thread.__init__(self)
        self.interface = None
        self.init_interface(config, device_no, hot_start)
        self.nchannels = self.interface.nchannels

        self.isKilled = self.nchannels*[False]

        self.config = config
        self.bias_now = self.nchannels*[-0]

        self.section_name = 'HV%d' % device_no
        self.bias_now = self.nchannels*[0]
        try:
            self.model_number = int(self.config.get(self.section_name, 'model'))
            self.ramp_speed = float(self.config.get(self.section_name, 'ramp'))
            self.target_bias = float(self.config.get(self.section_name, 'bias'))
            self.min_bias = float(self.config.get(self.section_name, 'min_bias'))
            self.max_bias = float(self.config.get(self.section_name, 'max_bias'))
            self.max_step = float(self.config.get(self.section_name, 'max_step'))
        except NoOptionError, err:
            print err, '--> exiting program'
            sys.exit(-1)
        if self.config.has_option('Names',self.section_name):
            self.__device_name = self.config.get('Names',self.section_name)
        else:
            self.__device_name = "UNKNOWN"


        self.isBusy = False
        self.maxTime = 20

        # evealuate hot start
        if hot_start:
            self.status = self.interface.get_output_status()
            self.update_voltage_current()
            voltage = self.get_bias()
            print voltage
            self.interface.set_bias(voltage)
            # self.immidiateVoltage = voltage
            self.target_bias = voltage
            self.bias_now = voltage
        else:
            self.status = 0
            self.bias_now = 0

        # last time the actual voltage was changed
        self.last_v_change = time()
        self.current_now = 0
        self.powering_down = False
        self.last_update = time()
        self.manual = False

        # make sure bias is consistent
        if self.max_bias < self.min_bias:
            raise Exception("Invalid config file (maxBias < minBias)")
        if self.target_bias < self.min_bias:
            raise Exception("Invalid config file (bias < minBias)")
        if self.target_bias > self.max_bias:
            raise Exception("Invalid config file (bias > maxBias)")

        # logging
        self.logger = logging.getLogger(self.section_name)
        self.fh = None
        self.configure_log()
        self.last_day = strftime('%d')
        self.last_status = self.status
        self.last_ramp = False

        # functions for CLI

    # ============================
    # INIT DEVICE INTERFACE
    def init_interface(self, config, device_no, hot_start):
        # if statements for model name
        try:
            print '\nInstantiation:', self.config.get(self.section_name, 'name')
        except NoOptionError:
            print '\nInstantiation:', self.section_name
        model = self.model_number
        self.isBusy = True
        if model == 2400 or model == 2410:
            self.interface = Keithley24XX(config, device_no, hot_start)
        elif model == (237 or 236 or 238):
            self.interface = Keithley23X(config, device_no, hot_start)
        elif model == '6517B' or model == 6517:
            self.interface = Keithley6517B(config, device_no, hot_start)
        elif model == '2657A' or model == 2657:
            self.interface = Keithley2657(config, device_no, hot_start)
        else:
            print "unknown model number: could not instantiate any device", '--> exiting program'
            sys.exit(-2)
        self.isBusy = False

    def get_device_name(self):
        return self.__device_name

    def set_device_name(self, device_name):
        if self.__device_name != device_name:
            self.__device_name = device_name
            self.create_new_log_file()
            print 'Setting device name of %s to "%s"'%(self.section_name,self.__device_name)

    # ============================
    # LOGGGING CONTROL
    def configure_log(self):
        logfile_dir = self.config.get('Main', 'testbeam_name') + '/'
        logfile_sub_dir = self.interface.name + '/'
        # check if dir exists
        dirs = os.path.dirname(logfile_dir)
        try:
            os.stat(dirs)
        except OSError:
            os.mkdir(dirs)
        dirs = os.path.dirname(logfile_dir + logfile_sub_dir)
        try:
            os.stat(dirs)
        except OSError:
            os.mkdir(dirs)
        logfile_name = self.interface.get_device_name(1) + strftime('_%Y_%m_%d_%H_%M_%S.log')
        logfile_dest = logfile_dir + logfile_sub_dir + logfile_name
        if self.fh:
            self.logger.removeHandler(self.fh)
        self.fh = logging.FileHandler(logfile_dest)
        self.fh.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s %(message)s', '%H:%M:%S')
        self.fh.setFormatter(formatter)
        self.logger.addHandler(self.fh)

    def create_new_log_file(self):
        self.logger.removeHandler(self.fh)
        self.configure_log()

    # make new logfile when the day changes
    def log_control(self):
        day = strftime('%d')
        if day != self.last_day:
            print 'a new day just begun...'
            self.create_new_log_file()
            self.last_day = day
            sleep(0.1)

    def add_log_entry(self, log_entry):
        if not self.logger:
            self.configure_log()
        self.logger.warning(log_entry+'\t%s'%self.__device_name)

    def write_log(self):
        # write when device is turned ON or OFF
        if self.get_last_status() != self.get_status():
            self.add_log_entry('DEVICE_ON') if self.get_status() else self.add_log_entry('DEVICE_OFF')
        self.last_status = self.get_status()
        # write when ramping starts
        if self.get_last_ramp() != self.is_ramping() and self.get_status():
            if self.is_ramping():
                log = 'START_RAMPING_AT' + '{0:7.1f}'.format(self.get_bias())
                self.add_log_entry(log)
                log = 'TARGET_BIAS' + '{0:7.1f}'.format(self.get_target_bias())
                self.add_log_entry(log)
        # only write measurements when device is ON
        if self.get_status():
            voltage = self.get_bias()
            current = self.get_current()
            string = "{0:10.3e} {1:10.3e}".format(voltage, current)
            self.add_log_entry(string)
        # write when ramping stops
        if self.get_last_ramp() != self.is_ramping() and self.get_status():
            if not self.is_ramping():
                log = 'FINISH_RAMPING_AT' + '{0:7.1f}'.format(self.get_bias())
                self.add_log_entry(log)
        self.last_ramp = self.is_ramping()

    # ============================
    # MAIN LOOP FOR THREAD (overwriting thread run)
    def run(self):
        while not self.isKilled:
            self.log_control()
            sleep(.1)
            if not self.manual:
                self.update_voltage_current()
                self.write_log()
                self.ramp()
                if self.is_ramping():
                    self.update_voltage_current()
                    sleep(.1)

    # ============================
    # GET-FUNCTIONS
    def get_current(self,ch=1):
        return self.current_now[ch]

    def get_bias(self,ch=1):
        return self.bias_now[ch]

    def get_target_bias(self,ch=1):
        return self.target_bias[ch]

    def get_status(self,ch=1):
        return self.status[ch]

    def get_last_status(self,ch=1):
        return self.last_status[ch]

    def get_update_time(self,ch=1):
        return self.last_update[ch]

    def get_last_ramp(self,ch=1):
        return self.last_ramp[ch]

    # ============================
    # SET-FUNCTIONS
    def set_target_bias(self, target,ch=1):
        self.target_bias[ch] = target
        self.last_v_change[ch] = time()
        log = 'SET_BIAS_TO' + '{0:7.1f}'.format(target)
        self.add_log_entry(log)

    def set_to_manual(self, status,ch=1):
        self.target_bias[ch] = self.interface.set_to_manual(status,ch)
        self.manual[ch] = status[ch]

    # ============================
    # MISCELLANEOUS FUNCTIONS
    def is_ramping(self,ch=1):
        return abs(self.bias_now[ch] - self.target_bias[ch]) > 0.1 if self.get_status(ch) else False

    def power_down(self,ch=1):
        self.set_target_bias(0,ch)
        self.powering_down[ch] = True

    def wait_for_device(self):
        now = time()
        while time() - now < self.maxTime and self.isBusy:
            sleep(.2)

    def update_voltage_current(self):
        #print 'update_voltage_current'
        self.wait_for_device()
        self.isBusy = True
        try:
            self.status = self.interface.get_output_status()
        except Exception as inst:
            print 'Couldn not update voltage/current- get output status:', inst
            self.isBusy = False
            return
        if self.status:
            #print 'status',self.status,
            try:
                if self.interface.nchannels == 1:
                    iv = self.interface.read_iv()
                    #print 'iv: ',iv,
                    self.bias_now[0] = iv['voltage']
                    #print 'bias_now',self.bias_now,
                    self.current_now[0] = iv['current']
                    #print 'current_now',self.current_now
                    self.last_update[0] = time()
                    #print 'readIV',voltage,current,self.targetBias,rest
                else:
                    ivs = self.interface.read_iv()
                    for iv in ivs:
                        ch = iv['channel']
                        self.bias_now[ch] = iv['voltage']
                        self.current_now[ch] = iv['current']

            except Exception as inst:
                print 'Could not read valid iv', type(inst), inst
        self.isBusy = False

    def ramp(self,ch=1):
        # Try to update voltage (we remember the measurement from the last loop)
        # (the step we can make in voltage is the ramp-speed times
        # how many seconds passed since last change)
        if not self.status[ch]:
            return
        #self.update_voltage_current()
        tries = 0
        while abs(self.interface.get_target_voltage(ch) - self.bias_now[ch]) > 1:
            msg =  '\033[91m'
            msg += 'Did not reach the current set voltage on the power supply:'
            msg += ' set_voltage: %f V'%self.interface.target_voltage 
            msg += ', measured_voltage: %f V'%self.bias_now
            msg += '\033[99m'
            print msg,'\033[99m'
            print "\033[99m" +' '+ '\033[0m'
            sleep(1)
            self.update_voltage_current()
            tries += 1
            if tries > 10:
                raise ValueError(msg)
                                
        delta_v = self.target_bias[ch] - self.interface.target_voltage[ch]

        #print 'target: %f \t bias: %f ==> %f V'%(self.target_bias,self.bias_now,delta_v)
        t_now = time()
        delta_t = t_now - self.last_v_change[ch]
        step_size = abs(self.ramp_speed[ch] * (delta_t))
        #print 'step_size: %f \t %f - %f = %f \t %f'%(step_size,t_now,self.last_v_change,delta_t,self.ramp_speed),

        # Limit the maximal voltage step size
        if step_size > self.max_step[ch]:
            step_size = self.max_step[ch]
        #print step_size

        # print 'delta U ',delta_v,step_size
        newtime = time()
        if abs(delta_v) > 0.1:
            if abs(delta_v) <= step_size:
                new_bias = self.target_bias[ch]
            else:
                new_bias = self.bias_now[ch] + copysign(step_size, delta_v)
            #print 'new bias: ',new_bias
            self.isBusy = True
            self.interface.set_voltage(new_bias,ch)
            if new_bias == self.target_bias[ch] and not self.powering_down[ch]:
                print '%s is done with ramping to %d' % (self.interface.name, self.target_bias)
            self.last_v_change[ch] = newtime
            self.isBusy = False
        if self.powering_down[ch] and abs(self.bias_now[ch]) < .1:
            self.interface.set_output(0,ch)
            self.powering_down[ch] = False
            print '%s has ramped down and turned off' % self.interface.name
            # End of ramp

# ============================
# MAIN
# ============================
if __name__ == '__main__':
    conf = ConfigParser()
    conf.read('config/keithley.cfg')
    keithley1 = HVDevice(conf, 6, False)
    #keithley2 = HVDevice(conf, 2, False)
    keithley1.logger.warning("HALLO")
    #keithley2.logger.warning("HALLO")
