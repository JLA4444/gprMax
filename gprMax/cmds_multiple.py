# Copyright (C) 2015-2019: The University of Edinburgh
#                 Authors: Craig Warren and Antonis Giannopoulos
#
# This file is part of gprMax.
#
# gprMax is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# gprMax is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with gprMax.  If not, see <http://www.gnu.org/licenses/>.

import logging
import sys

import numpy as np

import gprMax.config as config
from .cmds_geometry.cmds_geometry import UserObjectGeometry
from .exceptions import CmdInputError
from .geometry_outputs import GeometryObjects as GeometryObjectsUser
from .materials import Material as MaterialUser
from .materials import PeplinskiSoil as PeplinskiSoilUser
from .pml import CFSParameter
from .pml import CFS
from .receivers import Rx as RxUser
from .snapshots import Snapshot as SnapshotUser
from .sources import VoltageSource as VoltageSourceUser
from .sources import HertzianDipole as HertzianDipoleUser
from .sources import MagneticDipole as MagneticDipoleUser
from .sources import TransmissionLine as TransmissionLineUser
from .subgrids.base import SubGridBase
from .utilities import round_value
from .waveforms import Waveform as WaveformUser

log = logging.getLogger(__name__)


class UserObjectMulti:
    """Object that can occur multiple times in a model."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.order = None
        self.hash = '#example'
        self.autotranslate = True


    def __str__(self):
        """Readable user string as per hash commands."""
        s = ''
        for k, v in self.kwargs.items():
            if isinstance(v, tuple) or isinstance(v, list):
                v = ' '.join([str(el) for el in v])
            s += str(v) + ' '

        return f'{self.hash}: {s[:-1]}'

    def create(self, grid, uip):
        """Create the object and add it to the grid."""
        pass

    def params_str(self):
        """Readble string of parameters given to object."""
        return self.hash + ': ' + str(self.kwargs)


class Waveform(UserObjectMulti):
    """Allows you to specify waveforms to use with sources in the model.

    :param wave_type: wave type (see main documentation)
    :type wave_type: str, non-optional
    :param amp:  The scaling of the maximum amplitude of the waveform
    :type amp: float, non-optional
    :param freq: The centre frequency of the waveform (Hertz)
    :type freq: float, non-optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 0
        self.hash = '#waveform'

    def create(self, grid, uip):
        try:
            wavetype = self.kwargs['wave_type'].lower()
            amp = self.kwargs['amp']
            freq = self.kwargs['freq']
            ID = self.kwargs['id']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires exactly four parameters")

        if wavetype not in WaveformUser.types:
            raise CmdInputError(f"'{self.params_str()}' must have one of the following types {','.join(WaveformUser.types)}")
        if freq <= 0:
            raise CmdInputError(f"'{self.params_str()}' requires an excitation frequency value of greater than zero")
        if any(x.ID == ID for x in grid.waveforms):
            raise CmdInputError(f"'{self.params_str()}' with ID {ID} already exists")

        w = WaveformUser()
        w.ID = ID
        w.type = wavetype
        w.amp = amp
        w.freq = freq

        log.info(f'Waveform {w.ID} of type {w.type} with maximum amplitude scaling {w.amp:g}, frequency {w.freq:g}Hz created.')

        grid.waveforms.append(w)


class VoltageSource(UserObjectMulti):
    """Allows you to introduce a voltage source at an electric field location.

    :param polarisation: Polarisation of the source x, y, z
    :type polarisation: str, non-optional
    :param p1:  Position of the source x, y, z
    :type p1: list, non-optional
    :param resistance: Is the internal resistance of the voltage source in Ohms
    :type resistance: float, non-optional
    :param waveform_id: The identifier of the waveform that should be used with the source.
    :type waveform_id: str, non-optional
    :param start: Time to delay to start the source
    :type start: float, optional
    :param stop: Time to remove the source
    :type stop: float, optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 1
        self.hash = '#voltage_source'

    def create(self, grid, uip):
        try:
            p1 = self.kwargs['p1']
            polarisation = self.kwargs['polarisation'].lower()
            resistance = self.kwargs['resistance']
            waveform_id = self.kwargs['waveform_id']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires at least six parameters")

        # Check polarity & position parameters
        if polarisation not in ('x', 'y', 'z'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be x, y, or z")
        if '2D TMx' in grid.mode and (polarisation == 'y' or polarisation == 'z'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be x in 2D TMx mode")
        elif '2D TMy' in grid.mode and (polarisation == 'x' or polarisation == 'z'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be y in 2D TMy mode")
        elif '2D TMz' in grid.mode and (polarisation == 'x' or polarisation == 'y'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be z in 2D TMz mode")

        xcoord, ycoord, zcoord = uip.check_src_rx_point(p1, self.params_str())

        if resistance < 0:
            raise CmdInputError(f"'{self.params_str()}' requires a source resistance of zero or greater")

        # Check if there is a waveformID in the waveforms list
        if not any(x.ID == waveform_id for x in grid.waveforms):
            raise CmdInputError(f"'{self.params_str()}' there is no waveform with the identifier {tmp[5]}")

        v = VoltageSourceUser()
        v.polarisation = polarisation
        v.xcoord = xcoord
        v.ycoord = ycoord
        v.zcoord = zcoord
        v.ID = v.__class__.__name__ + '(' + str(v.xcoord) + ',' + str(v.ycoord) + ',' + str(v.zcoord) + ')'
        v.resistance = resistance
        v.waveformID = waveform_id

        try:
            start = self.kwargs['start']
            stop = self.kwargs['stop']
            # Check source start & source remove time parameters
            if start < 0:
                raise CmdInputError(f"'{self.params_str()}' delay of the initiation of the source should not be less than zero")
            if stop < 0:
                raise CmdInputError(f"'{self.params_str()}' time to remove the source should not be less than zero")
            if stop - start <= 0:
                raise CmdInputError(f"'{self.params_str()}' duration of the source should not be zero or less")
            v.start = start
            if stop > grid.timewindow:
                v.stop = grid.timewindow
            else:
                v.stop = stop
            startstop = f' start time {v.start:g} secs, finish time {v.stop:g} secs '
        except KeyError:
            v.start = 0
            v.stop = grid.timewindow
            startstop = ' '

        v.calculate_waveform_values(grid)

        log.info('Voltage source with polarity {} at {:g}m, {:g}m, {:g}m, resistance {:.1f} Ohms,'.format(v.polarisation, v.xcoord * grid.dx, v.ycoord * grid.dy, v.zcoord * grid.dz, v.resistance) + startstop + 'using waveform {} created.'.format(v.waveformID))

        grid.voltagesources.append(v)


class HertzianDipole(UserObjectMulti):
    """Allows you to specify a current density term at an electric field location
    - the simplest excitation, often referred to as an additive or soft source.

    :param polarisation: Polarisation of the source x, y, z
    :type polarisation: str, non-optional
    :param p1:  Position of the source x, y, z
    :type p1: list, non-optional
    :param waveform_id: The identifier of the waveform that should be used with the source.
    :type waveform_id: str, non-optional
    :param start: Time to delay to start the source
    :type start: float, optional
    :param stop: Time to remove the source
    :type stop: float, optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 2
        self.hash = '#hertzian_dipole'

    def create(self, grid, uip):
        try:
            polarisation = self.kwargs['polarisation'].lower()
            p1 = self.kwargs['p1']
            waveform_id = self.kwargs['waveform_id']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires at least 3 parameters")

        # Check polarity & position parameters
        if polarisation not in ('x', 'y', 'z'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be x, y, or z")
        if '2D TMx' in grid.mode and (polarisation == 'y' or polarisation == 'z'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be x in 2D TMx mode")
        elif '2D TMy' in grid.mode and (polarisation == 'x' or polarisation == 'z'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be y in 2D TMy mode")
        elif '2D TMz' in grid.mode and (polarisation == 'x' or polarisation == 'y'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be z in 2D TMz mode")

        xcoord, ycoord, zcoord = uip.check_src_rx_point(p1, self.params_str())

        # Check if there is a waveformID in the waveforms list
        if not any(x.ID == waveform_id for x in grid.waveforms):
            raise CmdInputError(f"'{self.params_str()}' there is no waveform with the identifier {tmp[4]}")

        h = HertzianDipoleUser()
        h.polarisation = polarisation

        # Set length of dipole to grid size in polarisation direction
        if h.polarisation == 'x':
            h.dl = grid.dx
        elif h.polarisation == 'y':
            h.dl = grid.dy
        elif h.polarisation == 'z':
            h.dl = grid.dz

        h.xcoord = xcoord
        h.ycoord = ycoord
        h.zcoord = zcoord
        h.xcoordorigin = xcoord
        h.ycoordorigin = ycoord
        h.zcoordorigin = zcoord
        h.ID = h.__class__.__name__ + '(' + str(h.xcoord) + ',' + str(h.ycoord) + ',' + str(h.zcoord) + ')'
        h.waveformID = waveform_id

        try:
            # Check source start & source remove time parameters
            start = self.kwargs['start']
            stop = self.kwargs['stop']
            if start < 0:
                raise CmdInputError(f"'{self.params_str()}' delay of the initiation of the source should not be less than zero")
            if stop < 0:
                raise CmdInputError(f"'{self.params_str()}' time to remove the source should not be less than zero")
            if stop - start <= 0:
                raise CmdInputError(f"'{self.params_str()}' duration of the source should not be zero or less")
            h.start = start
            if stop > grid.timewindow:
                h.stop = grid.timewindow
            else:
                h.stop = stop
            startstop = f' start time {h.start:g} secs, finish time {h.stop:g} secs '
        except KeyError:
            h.start = 0
            h.stop = grid.timewindow
            startstop = ' '

        h.calculate_waveform_values(grid)

        if grid.mode == '2D':
            log.info(f'Hertzian dipole is a line source in 2D with polarity {h.polarisation} at {h.xcoord * grid.dx:g}m, {h.ycoord * grid.dy:g}m, {h.zcoord * grid.dz:g}m,' + startstop + f'using waveform {h.waveformID} created.')
        else:
            log.info(f'Hertzian dipole with polarity {h.polarisation} at {h.xcoord * grid.dx:g}m, {h.ycoord * grid.dy:g}m, {h.zcoord * grid.dz:g}m,' + startstop + f'using waveform {h.waveformID} created.')

        grid.hertziandipoles.append(h)


class MagneticDipole(UserObjectMulti):
    """This will simulate an infinitesimal magnetic dipole. This is often referred
    to as an additive or soft source.

    :param polarisation: Polarisation of the source x, y, z
    :type polarisation: str, non-optional
    :param p1:  Position of the source x, y, z
    :type p1: list, non-optional
    :param waveform_id: The identifier of the waveform that should be used with the source.
    :type waveform_id: str, non-optional
    :param start: Time to delay to start the source
    :type start: float, optional
    :param stop: Time to remove the source
    :type stop: float, optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 3
        self.hash = '#magnetic_dipole'

    def create(self, grid, uip):
        try:
            polarisation = self.kwargs['polarisation'].lower()
            p1 = self.kwargs['p1']
            waveform_id = self.kwargs['waveform_id']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires at least five parameters")

        # Check polarity & position parameters
        if polarisation not in ('x', 'y', 'z'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be x, y, or z")
        if '2D TMx' in grid.mode and (polarisation == 'y' or polarisation == 'z'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be x in 2D TMx mode")
        elif '2D TMy' in grid.mode and (polarisation == 'x' or polarisation == 'z'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be y in 2D TMy mode")
        elif '2D TMz' in grid.mode and (polarisation == 'x' or polarisation == 'y'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be z in 2D TMz mode")

        xcoord, ycoord, zcoord = uip.check_src_rx_point(p1, self.params_str())

        # Check if there is a waveformID in the waveforms list
        if not any(x.ID == waveform_id for x in grid.waveforms):
            raise CmdInputError(f"'{self.params_str()}' there is no waveform with the identifier {waveform_id}")

        m = MagneticDipoleUser()
        m.polarisation = polarisation
        m.xcoord = xcoord
        m.ycoord = ycoord
        m.zcoord = zcoord
        m.xcoordorigin = xcoord
        m.ycoordorigin = ycoord
        m.zcoordorigin = zcoord
        m.ID = m.__class__.__name__ + '(' + str(m.xcoord) + ',' + str(m.ycoord) + ',' + str(m.zcoord) + ')'
        m.waveformID = waveform_id

        try:
            # Check source start & source remove time parameters
            start = self.kwargs['start']
            stop = self.kwargs['stop']
            if start < 0:
                raise CmdInputError(f"'{self.params_str()}' delay of the initiation of the source should not be less than zero")
            if stop < 0:
                raise CmdInputError(f"'{self.params_str()}' time to remove the source should not be less than zero")
            if stop - start <= 0:
                raise CmdInputError(f"'{self.params_str()}' duration of the source should not be zero or less")
            m.start = start
            if stop > grid.timewindow:
                m.stop = grid.timewindow
            else:
                m.stop = stop
            startstop = f' start time {m.start:g} secs, finish time {m.stop:g} secs '
        except KeyError:
            m.start = 0
            m.stop = grid.timewindow
            startstop = ' '

        m.calculate_waveform_values(grid)

        log.info(f'Magnetic dipole with polarity {m.polarisation} at {m.xcoord * grid.dx:g}m, {m.ycoord * grid.dy:g}m, {m.zcoord * grid.dz:g}m,' + startstop + f'using waveform {m.waveformID} created.')

        grid.magneticdipoles.append(m)


class TransmissionLine(UserObjectMulti):
    """Allows you to introduce a one-dimensional transmission line model
    at an electric field location

    :param polarisation: Polarisation of the source x, y, z
    :type polarisation: str, non-optional
    :param p1:  Position of the source x, y, z
    :type p1: list, non-optional
    :param resistance: Is the internal resistance of the voltage source in Ohms
    :type resistance: float, non-optional
    :param waveform_id: The identifier of the waveform that should be used with the source.
    :type waveform_id: str, non-optional
    :param start: Time to delay to start the source
    :type start: float, optional
    :param stop: Time to remove the source
    :type stop: float, optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 4
        self.hash = '#transmission_line'

    def create(self, grid, uip):
        try:
            polarisation = self.kwargs['polarisation'].lower()
            p1 = self.kwargs['p1']
            waveform_id = self.kwargs['waveform_id']
            resistance = self.kwargs['resistance']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires at least six parameters")

        # Warn about using a transmission line on GPU
        if grid.gpu is not None:
            raise CmdInputError(f"'{self.params_str()}' A #transmission_line cannot currently be used with GPU solving. Consider using a #voltage_source instead.")

        # Check polarity & position parameters
        if polarisation not in ('x', 'y', 'z'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be x, y, or z")
        if '2D TMx' in grid.mode and (polarisation == 'y' or polarisation == 'z'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be x in 2D TMx mode")
        elif '2D TMy' in grid.mode and (polarisation == 'x' or polarisation == 'z'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be y in 2D TMy mode")
        elif '2D TMz' in grid.mode and (polarisation == 'x' or polarisation == 'y'):
            raise CmdInputError(f"'{self.params_str()}' polarisation must be z in 2D TMz mode")

        xcoord, ycoord, zcoord = uip.check_src_rx_point(p1, self.params_str())

        if resistance <= 0 or resistance >= z0:
            raise CmdInputError(f"'{self.params_str()}' requires a resistance greater than zero and less than the impedance of free space, i.e. 376.73 Ohms")

        # Check if there is a waveformID in the waveforms list
        if not any(x.ID == waveform_id for x in grid.waveforms):
            raise CmdInputError(f"'{self.params_str()}' there is no waveform with the identifier {tmp[5]}")

        t = TransmissionLineUser(grid)
        t.polarisation = polarisation
        t.xcoord = xcoord
        t.ycoord = ycoord
        t.zcoord = zcoord
        t.ID = t.__class__.__name__ + '(' + str(t.xcoord) + ',' + str(t.ycoord) + ',' + str(t.zcoord) + ')'
        t.resistance = resistance
        t.waveformID = waveform_id

        try:
            # Check source start & source remove time parameters
            start = self.kwargs['start']
            stop = self.kwargs['stop']
            if start < 0:
                raise CmdInputError(f"'{self.params_str()}' delay of the initiation of the source should not be less than zero")
            if stop < 0:
                raise CmdInputError(f"'{self.params_str()}' time to remove the source should not be less than zero")
            if stop - start <= 0:
                raise CmdInputError(f"'{self.params_str()}' duration of the source should not be zero or less")
            t.start = start
            if stop > grid.timewindow:
                t.stop = grid.timewindow
            else:
                t.stop = stop
            startstop = f' start time {t.start:g} secs, finish time {t.stop:g} secs '
        except KeyError:
            t.start = 0
            t.stop = grid.timewindow
            startstop = ' '

        t.calculate_waveform_values(grid)
        t.calculate_incident_V_I(grid)

        log.info(f'Transmission line with polarity {t.polarisation} at {t.xcoord * grid.dx:g}m, {t.ycoord * grid.dy:g}m, {t.zcoord * grid.dz:g}m, resistance {t.resistance:.1f} Ohms,' + startstop + f'using waveform {t.waveformID} created.')

        grid.transmissionlines.append(t)


class Rx(UserObjectMulti):
    """Allows you to introduce output points into the model. These are locations
    where the values of the electric and magnetic field components over the number
    of iterations of the model will be saved to file. .

    :param p1: Position of the receiver x, y, z
    :type p1: list, non-optional
    :param id: Identifier for the receiver
    :type id: str, non-optional
    :param outputs: is a list of outputs with this receiver. It can be any
    selection from Ex, Ey, Ez, Hx, Hy, Hz, Ix, Iy, or Iz.
    :type outputs: list, non-optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 5
        self.hash = '#rx'
        self.constructor = RxUser

    def create(self, grid, uip):
        try:
            p1 = self.kwargs['p1']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' has an incorrect number of parameters")

        p = uip.check_src_rx_point(p1, self.params_str())

        r = self.constructor()
        r.xcoord, r.ycoord, r.zcoord = p
        r.xcoordorigin, r.ycoordorigin, r.zcoordorigin = p

        try:
            r.ID = self.kwargs['id']
            outputs = self.kwargs['outputs']
            # Get allowable outputs
            if grid.gpu is not None:
                allowableoutputs = RxUser.gpu_allowableoutputs
            else:
                allowableoutputs = RxUser.allowableoutputs
            # Check and add field output names
            for field in outputs:
                if field in allowableoutputs:
                    r.outputs[field] = np.zeros(grid.iterations, dtype=config.dtypes['float_or_double'])
                else:
                    raise CmdInputError(f"'{self.params_str()}' contains an output type that is not allowable. Allowable outputs in current context are {allowableoutputs}")
        # If no ID or outputs are specified, use default
        except KeyError:
            r.ID = r.__class__.__name__ + '(' + str(r.xcoord) + ',' + str(r.ycoord) + ',' + str(r.zcoord) + ')'
            for key in RxUser.defaultoutputs:
                r.outputs[key] = np.zeros(grid.iterations, dtype=config.dtypes['float_or_double'])

        log.info(f"Receiver at {r.xcoord * grid.dx:g}m, {r.ycoord * grid.dy:g}m, {r.zcoord * grid.dz:g}m with output component(s) {', '.join(r.outputs)} created.")

        grid.rxs.append(r)

        return r


class RxArray(UserObjectMulti):
    """Provides a simple method of defining multiple output points in the model.

    :param p1: Position of first receiver x, y, z
    :type p1: list, non-optional
    :param p2: Position of last receiver x, y, z
    :type p2: list, non-optional
    :param dl: Receiver spacing dx, dy, dz
    :type dl: list, non-optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 6
        self.hash = '#rx_array'

    def create(self, grid, uip):
        try:
            p1 = self.kwargs['p1']
            p2 = self.kwargs['p2']
            dl = self.kwargs['dl']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires exactly 9 parameters")

        xs, ys, zs = uip.check_src_rx_point(p1, self.params_str(), 'lower')
        xf, yf, zf = uip.check_src_rx_point(p2, self.params_str(), 'upper')
        dx, dy, dz = uip.discretise_point(dl)

        if xs > xf or ys > yf or zs > zf:
            raise CmdInputError(f"'{self.params_str()}' the lower coordinates should be less than the upper coordinates")
        if dx < 0 or dy < 0 or dz < 0:
            raise CmdInputError(f"'{self.params_str()}' the step size should not be less than zero")
        if dx < 1:
            if dx == 0:
                dx = 1
            else:
                raise CmdInputError(f"'{self.params_str()}' the step size should not be less than the spatial discretisation")
        if dy < 1:
            if dy == 0:
                dy = 1
            else:
                raise CmdInputError(f"'{self.params_str()}' the step size should not be less than the spatial discretisation")
        if dz < 1:
            if dz == 0:
                dz = 1
            else:
                raise CmdInputError(f"'{self.params_str()}' the step size should not be less than the spatial discretisation")

        log.info(f'Receiver array {xs * grid.dx:g}m, {ys * grid.dy:g}m, {zs * grid.dz:g}m, to {xf * grid.dx:g}m, {yf * grid.dy:g}m, {zf * grid.dz:g}m with steps {dx * grid.dx:g}m, {dy * grid.dy:g}m, {dz * grid.dz:g}m')

        for x in range(xs, xf + 1, dx):
            for y in range(ys, yf + 1, dy):
                for z in range(zs, zf + 1, dz):
                    r = RxUser()
                    r.xcoord = x
                    r.ycoord = y
                    r.zcoord = z
                    r.xcoordorigin = x
                    r.ycoordorigin = y
                    r.zcoordorigin = z
                    r.ID = r.__class__.__name__ + '(' + str(x) + ',' + str(y) + ',' + str(z) + ')'
                    for key in RxUser.defaultoutputs:
                        r.outputs[key] = np.zeros(grid.iterations, dtype=config.dtypes['float_or_double'])
                    log.info(f"  Receiver at {r.xcoord * grid.dx:g}m, {r.ycoord * grid.dy:g}m, {r.zcoord * grid.dz:g}m with output component(s) {', '.join(r.outputs)} created.")
                    grid.rxs.append(r)


class Snapshot(UserObjectMulti):
    """Provides a simple method of defining multiple output points in the model.

    :param p1: The lower left (x,y,z) coordinates of the volume of the snapshot in metres.
    :type p1: list, non-optional
    :param p2: the upper right (x,y,z) coordinates of the volume of the snapshot in metres.
    :type p2: list, non-optional
    :param dl: are the spatial discretisation of the snapshot in metres.
    :type dl: list, non-optional
    :param filename: is the name of the file where the snapshot will be stored
    :type filename: str, non-optional
    :param time:  are the time in seconds (float) or the iteration number (integer) which denote the point in time at which the snapshot will be taken.
    :type time: float, non-optional
    :param iterations:  are the iteration number (integer) which denote the point in time at which the snapshot will be taken.
    :type iterations: int, non-optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 19
        self.hash = '#snapshot'

    def create(self, grid, uip):
        if isinstance(grid, SubGridBase):
            raise CmdInputError(f"'{self.params_str()}' do not add Snapshots to Subgrids.")
        try:
            p1 = self.kwargs['p1']
            p2 = self.kwargs['p2']
            dl = self.kwargs['dl']
            filename = self.kwargs['filename']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires exactly 11 parameters")

        p1, p2 = uip.check_box_points(p1, p2, self.params_str())
        xs, ys, zs = p1
        xf, yf, zf = p2
        dx, dy, dz = uip.discretise_point(dl)

        # If number of iterations given
        try:
            iterations = self.kwargs['iterations']
        # If real floating point value given
        except KeyError:
            try:
                time = self.kwargs['time']
            except KeyError:
                raise CmdInputError(f"'{self.params_str()}' requires exactly 5 parameters")
            if time > 0:
                iterations = round_value((time / grid.dt)) + 1
            else:
                raise CmdInputError(f"'{self.params_str()}' time value must be greater than zero")

        if dx < 0 or dy < 0 or dz < 0:
            raise CmdInputError(f"'{self.params_str()}' the step size should not be less than zero")
        if dx < 1 or dy < 1 or dz < 1:
            raise CmdInputError(f"'{self.params_str()}' the step size should not be less than the spatial discretisation")
        if iterations <= 0 or iterations > grid.iterations:
            raise CmdInputError(f"'{self.params_str()}' time value is not valid")

        # Replace with old style snapshots if there are subgrids
        #if grid.subgrids:
        #    from .snapshot_subgrid import Snapshot as SnapshotSub
        #    s = SnapshotSub(xs, ys, zs, xf, yf, zf, dx, dy, dz, iterations, filename)
        #else:
        s = SnapshotUser(xs, ys, zs, xf, yf, zf, dx, dy, dz, iterations, filename)

        log.info(f'Snapshot from {xs * grid.dx:g}m, {ys * grid.dy:g}m, {zs * grid.dz:g}m, to {xf * grid.dx:g}m, {yf * grid.dy:g}m, {zf * grid.dz:g}m, discretisation {dx * grid.dx:g}m, {dy * grid.dy:g}m, {dz * grid.dz:g}m, at {s.time * grid.dt:g} secs with filename {s.basefilename} created.')

        grid.snapshots.append(s)


class Material(UserObjectMulti):
    """Allows you to introduce a material into the model described by a set of constitutive parameters.

    :param er: The relative permittivity.
    :type er: float, non-optional
    :param se: The conductivity (Siemens/metre).
    :type se: float, non-optional
    :param mr: The relative permeability.
    :type mr: float, non-optional
    :param sm: The magnetic loss.
    :type sm: float, non-optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 8
        self.hash = '#material'

    def create(self, grid, uip):
        try:
            er = self.kwargs['er']
            se = self.kwargs['se']
            mr = self.kwargs['mr']
            sm = self.kwargs['sm']
            material_id = self.kwargs['id']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires exactly five parameters")

        if er < 1:
            raise CmdInputError(f"'{self.params_str()}' requires a positive value of one or greater for static (DC) permittivity")
        if se != 'inf':
            se = float(se)
            if se < 0:
                raise CmdInputError(f"'{self.params_str()}' requires a positive value for conductivity")
        else:
            se = float('inf')
        if mr < 1:
            raise CmdInputError(f"'{self.params_str()}' requires a positive value of one or greater for permeability")
        if sm < 0:
            raise CmdInputError(f"'{self.params_str()}' requires a positive value for magnetic conductivity")
        if any(x.ID == material_id for x in grid.materials):
            raise CmdInputError(f"'{self.params_str()}' with ID {material_id} already exists")

        # Create a new instance of the Material class material (start index after pec & free_space)
        m = MaterialUser(len(grid.materials), material_id)
        m.er = er
        m.se = se
        m.mr = mr
        m.sm = sm

        # Set material averaging to False if infinite conductivity, i.e. pec
        if m.se == float('inf'):
            m.averagable = False

        log.info(f'Material {m.ID} with eps_r={m.er:g}, sigma={m.se:g} S/m; mu_r={m.mr:g}, sigma*={m.sm:g} Ohm/m created.')

        # Append the new material object to the materials list
        grid.materials.append(m)


class AddDebyeDispersion(UserObjectMulti):
    """Allows you to add dispersive properties to an already defined :class:`Material` based on a multiple pole Debye formulation

    :param n_poles: The number of Debye poles.
    :type n_poles: float, non-optional
    :param er_delta: The difference between the zero-frequency relative permittivity and the relative permittivity at infinite frequency for each pole
    :type er_delta: list, non-optional
    :param tau: The relaxation time for each pole.
    :type tau: list, non-optional
    :param material_ids: Material ids to apply disperive properties.
    :type material_ids: list, non-optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 9
        self.hash = '#add_dispersion_debye'

    def create(self, grid, uip):
        try:
            poles = self.kwargs['n_poles']
            er_delta = self.kwargs['er_delta']
            tau = self.kwargs['tau']
            material_ids = self.kwargs['material_ids']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires at least four parameters")

        if poles < 0:
            raise CmdInputError(f"'{self.params_str()}' requires a positive value for number of poles")

        # Look up requested materials in existing list of material instances
        materials = [y for x in material_ids for y in grid.materials if y.ID == x]

        if len(materials) != len(material_ids):
            notfound = [x for x in material_ids if x not in materials]
            raise CmdInputError(f"'{self.params_str()}' material(s) {notfound} do not exist")

        for material in materials:
            material.type = 'debye'
            material.poles = poles
            material.averagable = False
            for i in range(0, poles):
                # N.B Not checking if relaxation times are greater than time-step
                if tau[i] > 0:
                    material.deltaer.append(er_delta[i])
                    material.tau.append(tau[i])
                else:
                    raise CmdInputError(f"'{self.params_str()}' requires positive values for the permittivity difference.")
            if material.poles > MaterialUser.maxpoles:
                MaterialUser.maxpoles = material.poles

            log.info(f"Debye disperion added to {material.ID} with delta_eps_r={', '.join('%4.2f' % deltaer for deltaer in material.deltaer)}, and tau={', '.join('%4.3e' % tau for tau in material.tau)} secs created.")


class AddLorentzDispersion(UserObjectMulti):
    """Allows you to add dispersive properties to an already defined :class:`Material` based on a multiple pole Lorentz formulation

    :param n_poles: The number of Lorentz poles.
    :type n_poles: float, non-optional
    :param er_delta: The difference between the zero-frequency relative permittivity and the relative permittivity at infinite frequency for each pole
    :type er_delta: list, non-optional
    :param omega: The frequency for each pole.
    :type omega: list, non-optional
    :param delta: The damping coefficient (Hertz) for each pole.
    :type delta: list, non-optional
    :param material_ids: Material ids to apply disperive properties.
    :type material_ids: list, non-optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 10
        self.hash = '#add_dispersion_lorentz'

    def create(self, grid, uip):
        try:
            poles = self.kwargs['n_poles']
            er_delta = self.kwargs['er_delta']
            tau = self.kwargs['omega']
            alpha = self.kwargs['delta']
            material_ids = self.kwargs['material_ids']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires at least five parameters")

        if poles < 0:
            raise CmdInputError(f"'{self.params_str()}' requires a positive value for number of poles")

        # Look up requested materials in existing list of material instances
        materials = [y for x in material_ids for y in grid.materials if y.ID == x]

        if len(materials) != len(material_ids):
            notfound = [x for x in material_ids if x not in materials]
            raise CmdInputError(f"'{self.params_str()}' material(s) {notfound} do not exist")

        for material in materials:
            material.type = 'lorentz'
            material.poles = poles
            material.averagable = False
            for i in range(0, poles):
                if er_delta[i] > 0 and tau[i] > grid.dt and alpha[i] > grid.dt:
                    material.deltaer.append(er_delta[i])
                    material.tau.append(tau[i])
                    material.alpha.append(alpha[i])
                else:
                    raise CmdInputError(f"'{self.params_str()}' requires positive values for the permittivity difference and frequencies, and associated times that are greater than the time step for the model.")
            if material.poles > MaterialUser.maxpoles:
                MaterialUser.maxpoles = material.poles

            log.info(f"Lorentz disperion added to {material.ID} with delta_eps_r={', '.join('%4.2f' % deltaer for deltaer in material.deltaer)}, omega={', '.join('%4.3e' % tau for tau in material.tau)} secs, and gamma={', '.join('%4.3e' % alpha for alpha in material.alpha)} created.")


class AddDrudeDispersion(UserObjectMulti):
    """Allows you to add dispersive properties to an already defined :class:`Material` based on a multiple pole Drude formulation

    :param n_poles: The number of Drude poles.
    :type n_poles: float, non-optional
    :param tau: The inverse of the relaxation time for each pole.
    :type tau: list, non-optional
    :param omega: The frequency for each pole.
    :type omega: list, non-optional
    :param alpha: The inverse of the relaxation time.
    :type alpha: list, non-optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 11
        self.hash = '#add_dispersion_Drude'

    def create(self, grid, uip):
        try:
            poles = self.kwargs['n_poles']
            tau = self.kwargs['tau']
            alpha = self.kwargs['alpha']
            material_ids = self.kwargs['material_ids']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires at least four parameters")

        if poles < 0:
            raise CmdInputError(f"'{self.params_str()}' requires a positive value for number of poles")

        # Look up requested materials in existing list of material instances
        materials = [y for x in material_ids for y in grid.materials if y.ID == x]

        if len(materials) != len(material_ids):
            notfound = [x for x in material_ids if x not in materials]
            raise CmdInputError(f"'{self.params_str()}' material(s) {notfound} do not exist")

        for material in materials:
            material.type = 'drude'
            material.poles = poles
            material.averagable = False
            for i in range(0, poles):
                if tau[i] > 0 and alpha[i] > grid.dt:
                    material.tau.append(tau[i])
                    material.alpha.append(alpha[i])
                else:
                    raise CmdInputError(f"'{self.params_str()}' requires positive values for the frequencies, and associated times that are greater than the time step for the model.")
            if material.poles > MaterialUser.maxpoles:
                MaterialUser.maxpoles = material.poles

            log.info(f"Drude disperion added to {material.ID} with omega={', '.join('%4.3e' % tau for tau in material.tau)} secs, and gamma={', '.join('%4.3e' % alpha for alpha in material.alpha)} secs created.")


class SoilPeplinski(UserObjectMulti):
    """Allows you to use a mixing model for soils proposed by Peplinski (http://dx.doi.org/10.1109/36.387598

    :param sand_fraction: The sand fraction of the soil.
    :type sand_fraction: float, non-optional
    :param clay_fraction: The clay from of the soil.
    :type clay_fraction: float, non-optional
    :param bulk_density: The bulk density of the soil in grams per centimetre cubed.
    :type bulk_density: float, non-optional
    :param sand_density: The density of the sand particles in the soil in grams per centimetre cubed.
    :type sand_density: float, non-optional
    :param water_fraction_lower: Lower boundary volumetric water fraction of the soil.
    :type water_fraction_lower: float, non-optional
    :param water_fraction_upper:  Upper boundary volumetric water fraction of the soil.
    :type water_fraction_upper: float, non-optional
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 12
        self.hash = '#soil_peplinski'

    def create(self, grid, uip):
        try:
            sand_fraction = self.kwargs['sand_fraction']
            clay_fraction = self.kwargs['clay_fraction']
            bulk_density = self.kwargs['bulk_density']
            sand_density = self.kwargs['sand_density']
            water_fraction_lower = self.kwargs['water_fraction_lower']
            water_fraction_upper = self.kwargs['water_fraction_upper']
            ID = self.kwargs['id']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires at exactly seven parameters")

        if sand_fraction < 0:
            raise CmdInputError(f"'{self.params_str()}' requires a positive value for the sand fraction")
        if clay_fraction < 0:
            raise CmdInputError(f"'{self.params_str()}' requires a positive value for the clay fraction")
        if bulk_density < 0:
            raise CmdInputError(f"'{self.params_str()}' requires a positive value for the bulk density")
        if sand_density < 0:
            raise CmdInputError(f"'{self.params_str()}' requires a positive value for the sand particle density")
        if water_fraction_lower < 0:
            raise CmdInputError(f"'{self.params_str()}' requires a positive value for the lower limit of the water volumetric fraction")
        if water_fraction_upper < 0:
            raise CmdInputError(f"'{self.params_str()}' requires a positive value for the upper limit of the water volumetric fraction")
        if any(x.ID == ID for x in grid.mixingmodels):
            raise CmdInputError(f"'{self.params_str()}' with ID {ID} already exists")

        # Create a new instance of the Material class material (start index after pec & free_space)
        s = PeplinskiSoilUser(ID, sand_fraction, clay_fraction, bulk_density, sand_density, (water_fraction_lower, water_fraction_upper))

        log.info(f'Mixing model (Peplinski) used to create {s.ID} with sand fraction {s.S:g}, clay fraction {s.C:g}, bulk density {s.rb:g}g/cm3, sand particle density {s.rs:g}g/cm3, and water volumetric fraction {s.mu[0]:g} to {s.mu[1]:g} created.')

        # Append the new material object to the materials list
        grid.mixingmodels.append(s)


class GeometryView(UserObjectMulti):
    """Allows you output to file(s) information about the geometry of model.

    :param p1: The lower left (x,y,z) coordinates of the volume of the geometry view in metres.
    :type p1: list, non-optional
    :param p2: The upper right (x,y,z) coordinates of the volume of the geometry view in metres.
    :type p2: list, non-optional
    :param dl: The spatial discretisation of the geometry view in metres.
    :type dl: list, non-optional
    :param output_type: can be either n (normal) or f (fine).
    :type output_type: str, non-optional
    :param filename: the filename of the file where the geometry view will be stored in the same directory as the input file.
    :type filename: str, non-optional

    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 18
        self.hash = '#geometry_view'
        self.multi_grid = False

    def geometry_view_constructor(self, grid, output_type):
        # Check if user want geometry output for all grids
        try:
            self.kwargs['multi_grid']
            # there is no voxel output for multi grid output
            if isinstance(grid, SubGridBase):
                    raise CmdInputError(f"'{self.params_str()}' do not add multi_grid output to subgrid user object. Please add to Scene")
            if output_type == 'n':
                raise CmdInputError(f"'{self.params_str()}' voxel output type (n) is not supported for multigrid output.")
            # Change constructor to the multi grid output
            from .geometry_outputs import GeometryViewFineMultiGrid as GeometryViewUser
            self.multi_grid = True
        except KeyError:
            self.multi_grid = False
            from .geometry_outputs import GeometryView as GeometryViewUser

        return GeometryViewUser

    def create(self, grid, uip):
        try:
            p1 = self.kwargs['p1']
            p2 = self.kwargs['p2']
            dl = self.kwargs['dl']
            output_type = self.kwargs['output_type'].lower()
            filename = self.kwargs['filename']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires exactly eleven parameters")

        GeometryViewUser = self.geometry_view_constructor(grid, output_type)

        p1, p2 = uip.check_box_points(p1, p2, self.params_str())
        xs, ys, zs = p1
        xf, yf, zf = p2

        dx, dy, dz = uip.discretise_point(dl)

        if dx < 0 or dy < 0 or dz < 0:
            raise CmdInputError(f"'{self.params_str()}' the step size should not be less than zero")
        if dx > grid.nx or dy > grid.ny or dz > grid.nz:
            raise CmdInputError(f"'{self.params_str()}' the step size should be less than the domain size")
        if dx < 1 or dy < 1 or dz < 1:
            raise CmdInputError(f"'{self.params_str()}' the step size should not be less than the spatial discretisation")
        if output_type != 'n' and output_type != 'f':
            raise CmdInputError(f"'{self.params_str()}' requires type to be either n (normal) or f (fine)")
        if output_type == 'f' and (dx * grid.dx != grid.dx or dy * grid.dy != grid.dy or dz * grid.dz != grid.dz):
            raise CmdInputError(f"'{self.params_str()}' requires the spatial discretisation for the geometry view to be the same as the model for geometry view of type f (fine)")

        # Set type of geometry file
        if output_type == 'n':
            fileext = '.vti'
        else:
            fileext = '.vtp'

        g = GeometryViewUser(xs, ys, zs, xf, yf, zf, dx, dy, dz, filename, fileext, grid)

        log.info(f'Geometry view from {xs * grid.dx:g}m, {ys * grid.dy:g}m, {zs * grid.dz:g}m, to {xf * grid.dx:g}m, {yf * grid.dy:g}m, {zf * grid.dz:g}m, discretisation {dx * grid.dx:g}m, {dy * grid.dy:g}m, {dz * grid.dz:g}m, multi_grid={self.multi_grid}, grid={grid.name}, with filename base {g.basefilename} created.')

        # Append the new GeometryView object to the geometry views list
        grid.geometryviews.append(g)


class GeometryObjectsWrite(UserObjectMulti):
    """Allows you to write geometry generated in a model to file.

    :param p1: The lower left (x,y,z) coordinates of the volume of the output in metres.
    :type p1: list, non-optional
    :param p2: The upper right (x,y,z) coordinates of the volume of the output in metres.
    :type p2: list, non-optional
    :param filename: the filename of the file where the output will be stored in the same directory as the input file.
    :type filename: str, non-optional

    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 14
        self.hash = '#geometry_objects_write'

    def create(self, grid, uip):
        try:
            p1 = self.kwargs['p1']
            p2 = self.kwargs['p2']
            filename = self.kwargs['filename']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires exactly seven parameters")

        p1, p2 = uip.check_box_points(p1, p2, self.params_str())
        x0, y0, z0 = p1
        x1, y1, z1 = p2

        g = GeometryObjectsUser(x0, y0, z0, x1, y1, z1, filename)

        log.info(f'Geometry objects in the volume from {p1[0] * grid.dx:g}m, {p1[1] * grid.dy:g}m, {p1[2] * grid.dz:g}m, to {p2[0] * grid.dx:g}m, {p2[1] * grid.dy:g}m, {p2[2] * grid.dz:g}m, will be written to {g.filename}, with materials written to {g.materialsfilename}')

        # Append the new GeometryView object to the geometry objects to write list
        grid.geometryobjectswrite.append(g)


class PMLCFS(UserObjectMulti):
    """Allows you to write geometry generated in a model to file.

    :param alphascalingprofile: The type of scaling to use for the CFS α parameter
    :type alphascalingprofile: float, non-optional
    :param alphascalingdirection: The direction of the scaling to use for the CFS α parameter.
    :type alphascalingdirection: float, non-optional
    :param alphamin: Minimum value for the CFS α parameter.
    :type alphamin: str, non-optional
    :param alphamax: Minimum value for the CFS α parameter.
    :type alphamax: str, non-optional
    :param kappascalingprofile: The type of scaling to use for the CFS κ parameter
    :type kappascalingprofile: float, non-optional
    :param kappascalingdirection: The direction of the scaling to use for the CFS κ parameter.
    :type kappascalingdirection: float, non-optional
    :param kappamin: Minimum value for the CFS κ parameter.
    :type kappamin: str, non-optional
    :param kappamax: Minimum value for the CFS κ parameter.
    :type kappamax: str, non-optional
    :param sigmascalingprofile: The type of scaling to use for the CFS σ parameter
    :type sigmascalingprofile: float, non-optional
    :param sigmascalingdirection: The direction of the scaling to use for the CFS σ parameter.
    :type sigmascalingdirection: float, non-optional
    :param sigmamin: Minimum value for the CFS σ parameter.
    :type sigmamin: str, non-optional
    :param sigmamax: Minimum value for the CFS σ parameter.
    :type sigmamax: str, non-optional
    """

    count = 0

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.order = 15
        self.hash = '#pml_cfs'
        PMLCFS.count += 1
        if PMLCFS.count == 2:
            raise CmdInputError(f"'{self.params_str()}' can only be used up to two times, for up to a 2nd order PML")

    def create(self, grid, uip):
        try:
            alphascalingprofile = self.kwargs['alphascalingprofile']
            alphascalingdirection = self.kwargs['alphascalingdirection']
            alphamin = self.kwargs['alphamin']
            alphamax = self.kwargs['alphamax']
            kappascalingprofile = self.kwargs['kappascalingprofile']
            kappascalingdirection = self.kwargs['kappascalingdirection']
            kappamin = self.kwargs['kappamin']
            kappamax = self.kwargs['kappamax']
            sigmascalingprofile = self.kwargs['sigmascalingprofile']
            sigmascalingdirection = self.kwargs['sigmascalingdirection']
            sigmamin = self.kwargs['sigmamin']
            sigmamax = self.kwargs['sigmamax']
        except KeyError:
            raise CmdInputError(f"'{self.params_str()}' requires exactly twelve parameters")

        if alphascalingprofile not in CFSParameter.scalingprofiles.keys() or kappascalingprofile not in CFSParameter.scalingprofiles.keys() or sigmascalingprofile not in CFSParameter.scalingprofiles.keys():
            raise CmdInputError(f"'{self.params_str()}' must have scaling type {','.join(CFSParameter.scalingprofiles.keys())}")
        if alphascalingdirection not in CFSParameter.scalingdirections or kappascalingdirection not in CFSParameter.scalingdirections or sigmascalingdirection not in CFSParameter.scalingdirections:
            raise CmdInputError(f"'{self.params_str()}' must have scaling type {','.join(CFSParameter.scalingdirections)}")
        if float(alphamin) < 0 or float(alphamax) < 0 or float(kappamin) < 0 or float(kappamax) < 0 or float(sigmamin) < 0:
            raise CmdInputError(f"'{self.params_str()}' minimum and maximum scaling values must be greater than zero")
        if float(kappamin) < 1:
            raise CmdInputError(f"'{self.params_str()}' minimum scaling value for kappa must be greater than or equal to one")

        cfsalpha = CFSParameter()
        cfsalpha.ID = 'alpha'
        cfsalpha.scalingprofile = alphascalingprofile
        cfsalpha.scalingdirection = alphascalingdirection
        cfsalpha.min = float(alphamin)
        cfsalpha.max = float(alphamax)
        cfskappa = CFSParameter()
        cfskappa.ID = 'kappa'
        cfskappa.scalingprofile = kappascalingprofile
        cfskappa.scalingdirection = kappascalingdirection
        cfskappa.min = float(kappamin)
        cfskappa.max = float(kappamax)
        cfssigma = CFSParameter()
        cfssigma.ID = 'sigma'
        cfssigma.scalingprofile = sigmascalingprofile
        cfssigma.scalingdirection = sigmascalingdirection
        cfssigma.min = float(sigmamin)
        if sigmamax == 'None':
            cfssigma.max = None
        else:
            cfssigma.max = float(sigmamax)
        cfs = CFS()
        cfs.alpha = cfsalpha
        cfs.kappa = cfskappa
        cfs.sigma = cfssigma

        log.info(f'PML CFS parameters: alpha (scaling: {cfsalpha.scalingprofile}, scaling direction: {cfsalpha.scalingdirection}, min: {cfsalpha.min:g}, max: {cfsalpha.max:g}), kappa (scaling: {cfskappa.scalingprofile}, scaling direction: {cfskappa.scalingdirection}, min: {cfskappa.min:g}, max: {cfskappa.max:g}), sigma (scaling: {cfssigma.scalingprofile}, scaling direction: {cfssigma.scalingdirection}, min: {cfssigma.min:g}, max: {cfssigma.max:g}) created.')

        grid.cfs.append(cfs)


class Subgrid(UserObjectMulti):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.children_multiple = []
        self.children_geometry = []

    def add(self, node):
        if isinstance(node, UserObjectMulti):
            self.children_multiple.append(node)
        elif isinstance(node, UserObjectGeometry):
            self.children_geometry.append(node)
        else:
            raise GeneralError('This object is unknown to gprMax')


class SubgridHSG(UserObjectMulti):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)