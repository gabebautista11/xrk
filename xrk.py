# Python AIM XRK file reader.
#
# Wraps the "Matlab" xrk DLL provided by AIM from
# https://www.aim-sportline.com/download/software/doc/how-to-access-xrk-files-data-without-aim-software_101.pdf
#
# Copyright (c) 2021, Adam Lazur <adam@lazur.org>
#

import bisect
from ctypes import *
import datetime
import functools
import os
import time

DLLNAME = os.path.join(os.path.dirname(__file__), 'MatLabXRK-2017-64-ReleaseU.dll')
XRKDLL = cdll.LoadLibrary(DLLNAME)

# Need to override the DLL function signatures for non-int return types
# awk '/EXPORTED_FUNCTION/ { if ($2 != "int") { print; }}' MatLabXRK.h 
XRKDLL.get_library_date.restype = c_char_p
XRKDLL.get_library_time.restype = c_char_p
XRKDLL.get_vehicle_name.restype = c_char_p
XRKDLL.get_track_name.restype = c_char_p
XRKDLL.get_racer_name.restype = c_char_p
XRKDLL.get_championship_name.restype = c_char_p
XRKDLL.get_venue_type_name.restype = c_char_p
class TimeStruct(Structure):
    _fields_ = [
        ("tm_sec", c_int),
        ("tm_min", c_int),
        ("tm_hour", c_int),
        ("tm_mday", c_int),
        ("tm_mon", c_int),
        ("tm_year", c_int),
        ("tm_wday", c_int),
        ("tm_yday", c_int),
        ("tm_isdst", c_int),
    ]
XRKDLL.get_date_and_time.restype = POINTER(TimeStruct)
XRKDLL.get_channel_name.restype = c_char_p
XRKDLL.get_channel_units.restype = c_char_p
XRKDLL.get_GPS_channel_name.restype = c_char_p
XRKDLL.get_GPS_channel_units.restype = c_char_p
XRKDLL.get_GPS_raw_channel_name.restype = c_char_p
XRKDLL.get_GPS_raw_channel_units.restype = c_char_p


# Data channel class
class XRKChannel():
    def __init__(self, name, idxf, idxc):
        self.name = name
        self.idxf = idxf
        self.idxc = idxc
        self.f_get_channel_units = XRKDLL.get_channel_units
        self.f_get_channel_samples_count = XRKDLL.get_channel_samples_count
        self.f_get_channel_samples = XRKDLL.get_channel_samples
        self.f_get_lap_channel_samples_count = XRKDLL.get_lap_channel_samples_count
        self.f_get_lap_channel_samples = XRKDLL.get_lap_channel_samples

    def __repr__(self):
        return f"{self.__class__.__name__}(name='{self.name}', idxf={self.idxf}, idxc={self.idxc})"

    def units(self):
        return self.f_get_channel_units(self.idxf, self.idxc)

    def samples(self, lap=None):
        sample_count = self.f_get_channel_samples_count(self.idxf, self.idxc)
        if lap:
            sample_count=self.f_get_lap_channel_samples_count(self.idxf, lap, self.idxc)

        if sample_count < 0:
            print(f"ERROR: get samples count returned {sample_count}")
            return ((), ())

        ptimes = (c_double * sample_count)()
        pvalues = (c_double * sample_count)()

        success = None
        if lap:
            success = self.f_get_lap_channel_samples(self.idxf, lap, self.idxc,
                                                     byref(ptimes),
                                                     byref(pvalues),
                                                     sample_count)
        else:
            success = self.f_get_channel_samples(self.idxf, self.idxc, byref(ptimes), 
                                                 byref(pvalues), sample_count)

        times = []
        samples = []
        for i in range(sample_count):
            # Sigh. The timestamps for all samples are in milliseconds, but if
            # you ask for a lap's worth of samples, it comes back with time in
            # seconds. This blob does the multiply munge on the returned data.
            #
            # The call to round( , 4) is to unmunge some of the fractional
            # seconds as they come back from the DLL
            if not lap:
                times.append(round(ptimes[i]/1000.0, 4))
            else:
                times.append(round(ptimes[i], 4))
            samples.append(pvalues[i])

        return [times, samples]


# These are the same as XRKChannel, just swizzle the function pointers to call
# the appropriate functions
class XRKGPSChannel(XRKChannel):
    def __init__(self, name, idxf, idxc):
        self.name = name
        self.idxf = idxf
        self.idxc = idxc
        self.f_get_channel_units = XRKDLL.get_GPS_channel_units
        self.f_get_channel_samples_count = XRKDLL.get_GPS_channel_samples_count
        self.f_get_channel_samples = XRKDLL.get_GPS_channel_samples
        self.f_get_lap_channel_samples_count = XRKDLL.get_lap_GPS_channel_samples_count
        self.f_get_lap_channel_samples = XRKDLL.get_lap_GPS_channel_samples
    # rest comes from generic parent


class XRKGPSrawChannel(XRKChannel):
    def __init__(self, name, idxf, idxc):
        self.name = name
        self.idxf = idxf
        self.idxc = idxc
        self.f_get_channel_units = XRKDLL.get_GPS_raw_channel_units
        self.f_get_channel_samples_count = XRKDLL.get_GPS_raw_channel_samples_count
        self.f_get_channel_samples = XRKDLL.get_GPS_raw_channel_samples
        self.f_get_lap_channel_samples_count = XRKDLL.get_lap_GPS_raw_channel_samples_count
        self.f_get_lap_channel_samples = XRKDLL.get_lap_GPS_raw_channel_samples
    # rest comes from generic parent


class XRK():
    def __init__(self, filename):
        self.filename = filename
        fileptr = c_char_p(os.path.abspath(f'{filename}').encode())
        self.idxf = XRKDLL.open_file(fileptr.value)
        # everything hinges off of idxf...
        assert(self.idxf > 0)

    def close(self):
        return XRKDLL.close_file_i(self.idxf) > 0

    def __repr__(self):
        return (f"XRK(datetime={self.datetime}, lapcount={self.lapcount}, "
                f"vehicle_name={self.vehicle_name}, "
                f"track_name={self.track_name}, racer_name={self.racer_name}, "
                f"championship_name={self.championship_name})")

    @functools.cached_property
    def vehicle_name(self):
        return XRKDLL.get_vehicle_name(self.idxf).decode('UTF-8')

    @functools.cached_property
    def track_name(self):
        return XRKDLL.get_track_name(self.idxf).decode('UTF-8')

    @functools.cached_property
    def racer_name(self):
        return XRKDLL.get_racer_name(self.idxf).decode('UTF-8')

    @functools.cached_property
    def championship_name(self):
        return XRKDLL.get_championship_name(self.idxf).decode('UTF-8')

    @functools.cached_property
    def venue_type(self):
        return XRKDLL.get_venue_type_name(self.idxf).decode('UTF-8')

    @functools.cached_property
    def datetime(self):
        # returns a pointer, so we grab the 1st (only) one
        t = XRKDLL.get_date_and_time(self.idxf)[0]
        mktime = time.mktime((t.tm_year+1900, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min,
                           t.tm_sec, t.tm_wday, t.tm_yday, t.tm_isdst))
        return datetime.datetime.fromtimestamp(mktime).strftime("%Y-%m-%d %H:%M:%S")

    @functools.cached_property
    def lapcount(self):
        return XRKDLL.get_laps_count(self.idxf)

    @functools.cached_property
    def channels(self):
        channels = {}
        for i in range(XRKDLL.get_channels_count(self.idxf)):
            name = XRKDLL.get_channel_name(self.idxf, i).decode('UTF-8')
            assert(name not in channels), "channel name collision!"
            channels[name] = XRKChannel(name, self.idxf, i)

        for i in range(XRKDLL.get_GPS_channels_count(self.idxf)):
            name = XRKDLL.get_GPS_channel_name(self.idxf, i).decode('UTF-8')
            assert(name not in channels), "channel name collision!"
            channels[name] = XRKGPSChannel(name, self.idxf, i)

        for i in range(XRKDLL.get_GPS_raw_channels_count(self.idxf)):
            name = XRKDLL.get_GPS_raw_channel_name(self.idxf, i).decode('UTF-8')
            assert(name not in channels), "channel name collision!"
            channels[name] = XRKGPSrawChannel(name, self.idxf, i)

        return channels

    @functools.cached_property
    def timedistance(self):
        '''compute the time distance vector using GPS Speed'''
        seconds, speeds = self.channels['GPS Speed'].samples()
        assert(len(seconds) == len(speeds)) # paranoia

        # distance is in m/s
        distance = [0, ]
        totdistance = 0
        for i in range(1, len(seconds)):
            timedelta = seconds[i]-seconds[i-1]
            traveled = timedelta*speeds[i]
            totdistance = totdistance + traveled
            distance.insert(i, totdistance)

        return (seconds, distance)

    @functools.cached_property
    def lap_info(self):
        pstart = c_double(0)
        pduration = c_double(0)

        data = []
        for i in range(self.lapcount):
            XRKDLL.get_lap_info(self.idxf, i, byref(pstart), byref(pduration))
            data.append((pstart.value, pduration.value))

        return data

def convert_time_to_distance(seconds, timedistance):
    """convert a list of seconds into a list of distances"""
    distances = []
    for second in seconds:
        idx = bisect.bisect_left(timedistance[0], second)
        if (second != timedistance[0][idx]):
            print(f"WARNING: couldn't find value {second}, closest was {timedistance[0][idx]}")
        distances.append(timedistance[1][idx])

    return distances
