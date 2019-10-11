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

from importlib import import_module
import logging
import sys

import gprMax.config as config
from .cuda.fields_updates import kernel_template_fields
from .cuda.snapshots import kernel_template_store_snapshot
from .cuda.source_updates import kernel_template_sources
from .cython.fields_updates_normal import update_electric
from .cython.fields_updates_normal import update_magnetic
from .fields_outputs import store_outputs
from .receivers import gpu_initialise_rx_arrays
from .receivers import gpu_get_rx_array
from .snapshots import Snapshot
from .snapshots import gpu_initialise_snapshot_array
from .snapshots import gpu_get_snapshot_array
from .sources import gpu_initialise_src_arrays
from .utilities import timer

log = logging.getLogger(__name__)


class CPUUpdates:
    """Defines update functions for CPU-based solver."""

    def __init__(self, G):
        """
        Args:
            G (FDTDGrid): Holds essential parameters describing the model.
        """
        self.grid = G
        self.dispersive_update_a = None
        self.dispersive_update_b = None

    def store_outputs(self):
        """Store field component values for every receiver and transmission line."""
        store_outputs(self.grid)

    def store_snapshots(self, iteration):
        """Store any snapshots.

        Args:
            iteration (int): iteration number.
        """
        for snap in self.grid.snapshots:
            if snap.time == iteration + 1:
                snap.store(self.grid)

    def update_magnetic(self):
        """Update magnetic field components."""
        update_magnetic(self.grid.nx,
                        self.grid.ny,
                        self.grid.nz,
                        config.hostinfo['ompthreads'],
                        self.grid.updatecoeffsH,
                        self.grid.ID,
                        self.grid.Ex,
                        self.grid.Ey,
                        self.grid.Ez,
                        self.grid.Hx,
                        self.grid.Hy,
                        self.grid.Hz)

    def update_magnetic_pml(self):
        """Update magnetic field components with the PML correction."""
        for pml in self.grid.pmls:
            pml.update_magnetic(self.grid)

    def update_magnetic_sources(self):
        """Update magnetic field components from sources."""
        for source in self.grid.transmissionlines + self.grid.magneticdipoles:
            source.update_magnetic(self.grid.iteration,
                                   self.grid.updatecoeffsH,
                                   self.grid.ID,
                                   self.grid.Hx,
                                   self.grid.Hy,
                                   self.grid.Hz,
                                   self.grid)

    def update_electric_a(self):
        """Update electric field components."""
        # All materials are non-dispersive so do standard update.
        if config.materials['maxpoles'] == 0:
            update_electric(self.grid.nx,
                            self.grid.ny,
                            self.grid.nz,
                            config.hostinfo['ompthreads'],
                            self.grid.updatecoeffsE,
                            self.grid.ID,
                            self.grid.Ex,
                            self.grid.Ey,
                            self.grid.Ez,
                            self.grid.Hx,
                            self.grid.Hy,
                            self.grid.Hz)

        # If there are any dispersive materials do 1st part of dispersive update
        # (it is split into two parts as it requires present and updated electric field values).
        else:
            self.dispersive_update_a(self.grid.nx,
                                     self.grid.ny,
                                     self.grid.nz,
                                     config.hostinfo['ompthreads'],
                                     config.materials['maxpoles'],
                                     self.grid.updatecoeffsE,
                                     self.grid.updatecoeffsdispersive,
                                     self.grid.ID,
                                     self.grid.Tx,
                                     self.grid.Ty,
                                     self.grid.Tz,
                                     self.grid.Ex,
                                     self.grid.Ey,
                                     self.grid.Ez,
                                     self.grid.Hx,
                                     self.grid.Hy,
                                     self.grid.Hz)

    def update_electric_pml(self):
        """Update electric field components with the PML correction."""
        for pml in self.grid.pmls:
            pml.update_electric(self.grid)

    def update_electric_sources(self):
        """Update electric field components from sources -
            update any Hertzian dipole sources last.
        """
        for source in self.grid.voltagesources + self.grid.transmissionlines + self.grid.hertziandipoles:
            source.update_electric(self.grid.iteration,
                                   self.grid.updatecoeffsE,
                                   self.grid.ID,
                                   self.grid.Ex,
                                   self.grid.Ey,
                                   self.grid.Ez,
                                   self.grid)
        self.grid.iteration += 1

    def update_electric_b(self):
        """If there are any dispersive materials do 2nd part of dispersive
            update - it is split into two parts as it requires present and
            updated electric field values. Therefore it can only be completely
            updated after the electric field has been updated by the PML and
            source updates.
        """
        if config.materials['maxpoles'] != 0:
            self.dispersive_update_b(self.grid.nx,
                                     self.grid.ny,
                                     self.grid.nz,
                                     config.hostinfo['ompthreads'],
                                     config.materials['maxpoles'],
                                     self.grid.updatecoeffsdispersive,
                                     self.grid.ID,
                                     self.grid.Tx,
                                     self.grid.Ty,
                                     self.grid.Tz,
                                     self.grid.Ex,
                                     self.grid.Ey,
                                     self.grid.Ez)

    def adapt_dispersive_config(self, config):
        """Set properties for disperive materials.

        Args:
            config ():

        Returns:
            props (Props): Dispersive material properties.
        """
        if config.materials['maxpoles'] > 1:
            poles = 'multi'
        else:
            poles = '1'

        if config.precision == 'single':
            type = 'float'
        else:
            type = 'double'

        if config.materials['dispersivedtype'] == config.dtypes['complex']:
            dispersion = 'complex'
        else:
            dispersion = 'real'

        class Props():
            pass

        props = Props()
        props.poles = poles
        props.precision = type
        props.dispersion_type = dispersion

        return props

    def set_dispersive_updates(self, props):
        """Set dispersive update functions.

        Args:
            props (Props): Dispersive material properties.
        """
        update_f = 'update_electric_dispersive_{}pole_{}_{}_{}'
        disp_a = update_f.format(props.poles, 'A', props.precision, props.dispersion_type)
        disp_b = update_f.format(props.poles, 'B', props.precision, props.dispersion_type)

        disp_a_f = getattr(import_module('gprMax.cython.fields_updates_dispersive'), disp_a)
        disp_b_f = getattr(import_module('gprMax.cython.fields_updates_dispersive'), disp_b)

        self.dispersive_update_a = disp_a_f
        self.dispersive_update_b = disp_b_f

    def time_start(self):
        self.timestart = timer()

    def calculate_tsolve(self):
        return timer() - self.timestart

    def finalise(self):
        pass

    def cleanup(self):
        pass


class CUDAUpdates:
    """Defines update functions for GPU-based (CUDA) solver."""

    def __init__(self, G):
        """
        Args:
            G (FDTDGrid): Holds essential parameters describing the model.
        """

        self.grid = G
        self.dispersive_update_a = None
        self.dispersive_update_b = None

        import pycuda.driver as drv
        from pycuda.compiler import SourceModule
        drv.init()

        # Suppress nvcc warnings on Windows
        log.debug('Move nvcc compiler options to simulation config')
        if sys.platform == 'win32':
            self.compiler_opts = ['-w']
        else:
            self.compiler_opts = None

        # Create device handle and context on specifc GPU device (and make it current context)
        self.dev = drv.Device(self.grid.gpu.deviceID)
        self.ctx = dev.make_context()

        # Initialise arrays on GPU, prepare kernels, and get kernel functions
        self.set_field_kernels()
        self.set_pml_kernels()
        self.set_rx_kernel()
        self.set_src_kernels()
        self.set_snapshot_kernel()

    def set_field_kernels(self):
        """Electric and magnetic field updates - prepare kernels, and
            get kernel functions.
        """
        if config.materials['maxpoles'] > 0:
            kernels_fields = SourceModule(kernels_template_fields.substitute(
                                          REAL=cudafloattype,
                                          COMPLEX=cudacomplextype,
                                          N_updatecoeffsE=self.grid.updatecoeffsE.size,
                                          N_updatecoeffsH=self.grid.updatecoeffsH.size,
                                          NY_MATCOEFFS=self.grid.updatecoeffsE.shape[1],
                                          NY_MATDISPCOEFFS=self.grid.updatecoeffsdispersive.shape[1],
                                          NX_FIELDS=self.grid.nx + 1,
                                          NY_FIELDS=self.grid.ny + 1,
                                          NZ_FIELDS=self.grid.nz + 1,
                                          NX_ID=self.grid.ID.shape[1],
                                          NY_ID=self.grid.ID.shape[2],
                                          NZ_ID=self.grid.ID.shape[3],
                                          NX_T=self.grid.Tx.shape[1],
                                          NY_T=self.grid.Tx.shape[2],
                                          NZ_T=self.grid.Tx.shape[3]),
                                          options=self.compiler_opts)
        else: # Set to one any substitutions for dispersive materials
            kernels_fields = SourceModule(kernels_template_fields.substitute(
                                          REAL=cudafloattype,
                                          COMPLEX=cudacomplextype,
                                          N_updatecoeffsE=self.grid.updatecoeffsE.size,
                                          N_updatecoeffsH=self.grid.updatecoeffsH.size,
                                          NY_MATCOEFFS=self.grid.updatecoeffsE.shape[1],
                                          NY_MATDISPCOEFFS=1,
                                          NX_FIELDS=self.grid.nx + 1,
                                          NY_FIELDS=self.grid.ny + 1,
                                          NZ_FIELDS=self.grid.nz + 1,
                                          NX_ID=self.grid.ID.shape[1],
                                          NY_ID=self.grid.ID.shape[2],
                                          NZ_ID=self.grid.ID.shape[3],
                                          NX_T=1,
                                          NY_T=1,
                                          NZ_T=1),
                                          options=self.compiler_opts)
        self.update_electric = kernels_fields.get_function("update_electric")
        self.update_magnetic = kernels_fields.get_function("update_magnetic")
        if self.grid.updatecoeffsE.nbytes + self.grid.updatecoeffsH.nbytes > self.grid.gpu.constmem:
            raise GeneralError(log.exception(f'Too many materials in the model to fit onto constant memory of size {human_size(self.grid.gpu.constmem)} on {self.grid.gpu.deviceID} - {self.grid.gpu.name} GPU'))
        self.copy_mat_coeffs()

        # Electric and magnetic field updates - dispersive materials - get kernel functions and initialise array on GPU
        if config.materials['maxpoles'] > 0:  # If there are any dispersive materials (updates are split into two parts as they require present and updated electric field values).
            self.dispersive_update_a = kernels_fields.get_function("update_electric_dispersive_A")
            self.dispersive_update_b = kernels_fields.get_function("update_electric_dispersive_B")
            self.grid.gpu_initialise_dispersive_arrays()

        # Electric and magnetic field updates - set blocks per grid and initialise field arrays on GPU
        self.grid.gpu_set_blocks_per_grid()
        self.grid.gpu_initialise_arrays()

    def set_pml_kernels(self):
        """PMLS - prepare kernels and get kernel functions."""
        if self.grid.pmls:
            pmlmodulelectric = 'gprMax.cuda.pml_updates_electric_' + self.grid.pmlformulation
            kernelelectricfunc = getattr(import_module(pmlmodulelectric),
                                         'kernels_template_pml_electric_' +
                                         self.grid.pmlformulation)
            pmlmodulemagnetic = 'gprMax.cuda.pml_updates_magnetic_' + self.grid.pmlformulation
            kernelmagneticfunc = getattr(import_module(pmlmodulemagnetic),
                                         'kernels_template_pml_magnetic_' +
                                         self.grid.pmlformulation)
            kernels_pml_electric = SourceModule(kernelelectricfunc.substitute(
                                                REAL=cudafloattype,
                                                N_updatecoeffsE=self.grid.updatecoeffsE.size,
                                                NY_MATCOEFFS=self.grid.updatecoeffsE.shape[1],
                                                NX_FIELDS=self.grid.nx + 1,
                                                NY_FIELDS=self.grid.ny + 1,
                                                NZ_FIELDS=self.grid.nz + 1,
                                                NX_ID=self.grid.ID.shape[1],
                                                NY_ID=self.grid.ID.shape[2],
                                                NZ_ID=self.grid.ID.shape[3]),
                                                options=self.compiler_opts)
            kernels_pml_magnetic = SourceModule(kernelmagneticfunc.substitute(
                                                REAL=cudafloattype,
                                                N_updatecoeffsH=self.grid.updatecoeffsH.size,
                                                NY_MATCOEFFS=self.grid.updatecoeffsH.shape[1],
                                                NX_FIELDS=self.grid.nx + 1,
                                                NY_FIELDS=self.grid.ny + 1,
                                                NZ_FIELDS=self.grid.nz + 1,
                                                NX_ID=self.gridG.ID.shape[1],
                                                NY_ID=self.grid.ID.shape[2],
                                                NZ_ID=self.grid.ID.shape[3]),
                                                options=self.compiler_opts)
            self.copy_mat_coeffs()
            # Set block per grid, initialise arrays on GPU, and get kernel functions
            for pml in self.grid.pmls:
                pml.gpu_initialise_arrays()
                pml.gpu_get_update_funcs(kernels_pml_electric, kernels_pml_magnetic)
                pml.gpu_set_blocks_per_grid(self.grid)

    def set_rx_kernel(self):
        """Receivers - initialise arrays on GPU, prepare kernel and get kernel
                        function.
        """
        if self.grid.rxs:
            rxcoords_gpu, rxs_gpu = gpu_initialise_rx_arrays(self.grid)
            kernel_store_outputs = SourceModule(kernel_template_store_outputs.substitute(
                                                REAL=cudafloattype,
                                                NY_RXCOORDS=3,
                                                NX_RXS=6,
                                                NY_RXS=self.grid.iterations,
                                                NZ_RXS=len(self.grid.rxs),
                                                NX_FIELDS=self.grid.nx + 1,
                                                NY_FIELDS=self.grid.ny + 1,
                                                NZ_FIELDS=self.grid.nz + 1),
                                                options=self.compiler_opts)
            self.store_outputs = kernel_store_outputs.get_function("store_outputs")

    def set_src_kernels(self):
        """Sources - initialise arrays on GPU, prepare kernel and get kernel
                        function.
        """
        if self.grid.voltagesources + self.grid.hertziandipoles + self.grid.magneticdipoles:
            kernels_sources = SourceModule(kernels_template_sources.substitute(
                                           REAL=cudafloattype,
                                           N_updatecoeffsE=self.grid.updatecoeffsE.size,
                                           N_updatecoeffsH=self.grid.updatecoeffsH.size,
                                           NY_MATCOEFFS=self.grid.updatecoeffsE.shape[1],
                                           NY_SRCINFO=4,
                                           NY_SRCWAVES=self.grid.iterations,
                                           NX_FIELDS=self.grid.nx + 1,
                                           NY_FIELDS=self.grid.ny + 1,
                                           NZ_FIELDS=self.grid.nz + 1,
                                           NX_ID=self.grid.ID.shape[1],
                                           NY_ID=self.grid.ID.shape[2],
                                           NZ_ID=self.grid.ID.shape[3]),
                                           options=self.compiler_opts)
            self.copy_mat_coeffs()
            if self.grid.hertziandipoles:
                self.srcinfo1_hertzian_gpu, self.srcinfo2_hertzian_gpu, self.srcwaves_hertzian_gpu = gpu_initialise_src_arrays(self.grid.hertziandipoles, self.grid)
                self.update_hertzian_dipole_gpu = kernels_sources.get_function("update_hertzian_dipole")
            if self.grid.magneticdipoles:
                self.srcinfo1_magnetic_gpu, self.srcinfo2_magnetic_gpu, self.srcwaves_magnetic_gpu = gpu_initialise_src_arrays(self.grid.magneticdipoles, self.grid)
                self.update_magnetic_dipole_gpu = kernels_sources.get_function("update_magnetic_dipole")
            if self.grid.voltagesources:
                self.srcinfo1_voltage_gpu, self.srcinfo2_voltage_gpu, self.srcwaves_voltage_gpu = gpu_initialise_src_arrays(self.grid.voltagesources, self.grid)
                self.update_voltage_source_gpu = kernels_sources.get_function("update_voltage_source")

    def set_snapshot_kernel(self):
        """Snapshots - initialise arrays on GPU, prepare kernel and get kernel
                        function.
        """
        if self.grid.snapshots:
            self.snapEx_gpu, self.snapEy_gpu, self.snapEz_gpu, self.snapHx_gpu, self.snapHy_gpu, self.snapHz_gpu = gpu_initialise_snapshot_array(self.grid)
            kernel_store_snapshot = SourceModule(kernel_template_store_snapshot.substitute(
                                                 REAL=cudafloattype,
                                                 NX_SNAPS=Snapshot.nx_max,
                                                 NY_SNAPS=Snapshot.ny_max,
                                                 NZ_SNAPS=Snapshot.nz_max,
                                                 NX_FIELDS=self.grid.nx + 1,
                                                 NY_FIELDS=self.grid.ny + 1,
                                                 NZ_FIELDS=self.grid.nz + 1),
                                                 options=self.compiler_opts)
            self.store_snapshot_gpu = kernel_store_snapshot.get_function("store_snapshot")

    def copy_mat_coeffs(self):
        """Copy material coefficient arrays to constant memory of GPU
            (must be <64KB).
        """
        updatecoeffsE = kernels_sources.get_global('updatecoeffsE')[0]
        updatecoeffsH = kernels_sources.get_global('updatecoeffsH')[0]
        self.drv.memcpy_htod(updatecoeffsE, self.grid.updatecoeffsE)
        self.drv.memcpy_htod(updatecoeffsH, self.grid.updatecoeffsH)

    def store_outputs(self):
        """Store field component values for every receiver and transmission line."""
        if self.grid.rxs:
            self.store_outputs(np.int32(len(self.grid.rxs)),
                               np.int32(self.grid.iteration),
                               self.rxcoords_gpu.gpudata,
                               self.rxs_gpu.gpudata,
                               self.grid.Ex_gpu.gpudata,
                               self.grid.Ey_gpu.gpudata,
                               self.grid.Ez_gpu.gpudata,
                               self.grid.Hx_gpu.gpudata,
                               self.grid.Hy_gpu.gpudata,
                               self.grid.Hz_gpu.gpudata,
                               block=(1, 1, 1),
                               grid=(round32(len(self.grid.rxs)), 1, 1))

    def store_snapshots(self, iteration):
        """Store any snapshots.

        Args:
            iteration (int): iteration number.
        """

        for i, snap in enumerate(self.grid.snapshots):
            if snap.time == iteration + 1:
                snapno = 0 if self.grid.snapsgpu2cpu else i
                self.store_snapshot_gpu(np.int32(snapno),
                                        np.int32(snap.xs),
                                        np.int32(snap.xf),
                                        np.int32(snap.ys),
                                        np.int32(snap.yf),
                                        np.int32(snap.zs),
                                        np.int32(snap.zf),
                                        np.int32(snap.dx),
                                        np.int32(snap.dy),
                                        np.int32(snap.dz),
                                        self.grid.Ex_gpu.gpudata,
                                        self.grid.Ey_gpu.gpudata,
                                        self.grid.Ez_gpu.gpudata,
                                        self.grid.Hx_gpu.gpudata,
                                        self.grid.Hy_gpu.gpudata,
                                        self.grid.Hz_gpu.gpudata,
                                        self.snapEx_gpu.gpudata,
                                        self.snapEy_gpu.gpudata,
                                        self.snapEz_gpu.gpudata,
                                        self.snapHx_gpu.gpudata,
                                        self.snapHy_gpu.gpudata,
                                        self.snapHz_gpu.gpudata,
                                        block=Snapshot.tpb,
                                        grid=Snapshot.bpg)
                if self.grid.snapsgpu2cpu:
                    gpu_get_snapshot_array(self.grid.snapEx_gpu.get(),
                                           self.grid.snapEy_gpu.get(),
                                           self.grid.snapEz_gpu.get(),
                                           self.grid.snapHx_gpu.get(),
                                           self.grid.snapHy_gpu.get(),
                                           self.grid.snapHz_gpu.get(),
                                           0,
                                           snap)

    def update_magnetic(self):
        """Update magnetic field components."""
        self.update_magnetic(np.int32(self.grid.nx),
                        np.int32(self.grid.ny),
                        np.int32(self.grid.nz),
                        self.grid.ID_gpu,
                        self.grid.Hx_gpu,
                        self.grid.Hy_gpu,
                        self.grid.Hz_gpu,
                        self.grid.Ex_gpu,
                        self.grid.Ey_gpu,
                        self.grid.Ez_gpu,
                        block=self.grid.tpb,
                        grid=self.grid.bpg)

    def update_magnetic_pml(self):
        """Update magnetic field components with the PML correction."""
        for pml in self.grid.pmls:
            pml.gpu_update_magnetic(self.grid)

    def update_magnetic_sources(self):
        """Update magnetic field components from sources."""
        if self.grid.magneticdipoles:
            self.update_magnetic_dipole_gpu(np.int32(len(self.grid.magneticdipoles)),
                                              np.int32(self.grid.iteration),
                                              config.dtypes['float_or_double'](self.grid.dx),
                                              config.dtypes['float_or_double'](self.grid.dy),
                                              config.dtypes['float_or_double'](self.grid.dz),
                                              self.srcinfo1_magnetic_gpu.gpudata,
                                              self.srcinfo2_magnetic_gpu.gpudata,
                                              self.srcwaves_magnetic_gpu.gpudata,
                                              self.grid.ID_gpu,
                                              self.grid.Hx_gpu,
                                              self.grid.Hy_gpu,
                                              self.grid.Hz_gpu,
                                              block=(1, 1, 1),
                                              grid=(round32(len(self.grid.magneticdipoles)), 1, 1))

    def update_electric_a(self):
        """Update electric field components."""
        # All materials are non-dispersive so do standard update.
        if config.materials['maxpoles'] == 0:
            self.update_electric(np.int32(self.grid.nx),
                                 np.int32(self.grid.ny),
                                 np.int32(self.grid.nz),
                                 self.grid.ID_gpu,
                                 self.grid.Ex_gpu,
                                 self.grid.Ey_gpu,
                                 self.grid.Ez_gpu,
                                 self.grid.Hx_gpu,
                                 self.grid.Hy_gpu,
                                 self.grid.Hz_gpu,
                                 block=self.grid.tpb,
                                 grid=self.grid.bpg)

        # If there are any dispersive materials do 1st part of dispersive update
        # (it is split into two parts as it requires present and updated electric field values).
        else:
            self.dispersive_update_a(np.int32(self.grid.nx),
                                     np.int32(self.grid.ny),
                                     np.int32(self.grid.nz),
                                     np.int32(config.materials['maxpoles']),
                                     self.grid.updatecoeffsdispersive_gpu.gpudata,
                                     self.grid.Tx_gpu,
                                     self.grid.Ty_gpu,
                                     self.grid.Tz_gpu,
                                     self.grid.ID_gpu,
                                     self.grid.Ex_gpu,
                                     self.grid.Ey_gpu,
                                     self.grid.Ez_gpu,
                                     self.grid.Hx_gpu,
                                     self.grid.Hy_gpu,
                                     self.grid.Hz_gpu,
                                     block=self.grid.tpb,
                                     grid=self.grid.bpg)

    def update_electric_pml(self):
        """Update electric field components with the PML correction."""
        for pml in self.grid.pmls:
            pml.gpu_update_electric(self.grid)

    def update_electric_sources(self):
        """Update electric field components from sources -
            update any Hertzian dipole sources last.
        """
        if self.grid.voltagesources:
            self.update_voltage_source_gpu(np.int32(len(self.grid.voltagesources)),
                                           np.int32(self.grid.iteration),
                                           config.dtypes['float_or_double'](self.grid.dx),
                                           config.dtypes['float_or_double'](self.grid.dy),
                                           config.dtypes['float_or_double'](self.grid.dz),
                                           self.srcinfo1_voltage_gpu.gpudata,
                                           self.srcinfo2_voltage_gpu.gpudata,
                                           self.srcwaves_voltage_gpu.gpudata,
                                           self.grid.ID_gpu,
                                           self.grid.Ex_gpu,
                                           self.grid.Ey_gpu,
                                           self.grid.Ez_gpu,
                                           block=(1, 1, 1),
                                           grid=(round32(len(self.grid.voltagesources)), 1, 1))

        if self.grid.hertziandipoles:
            self.update_hertzian_dipole_gpu(np.int32(len(self.grid.hertziandipoles)),
                                            np.int32(self.grid.iteration),
                                            config.dtypes['float_or_double'](self.grid.dx),
                                            config.dtypes['float_or_double'](self.grid.dy),
                                            config.dtypes['float_or_double'](self.grid.dz),
                                            self.srcinfo1_hertzian_gpu.gpudata,
                                            self.srcinfo2_hertzian_gpu.gpudata,
                                            self.srcwaves_hertzian_gpu.gpudata,
                                            self.grid.ID_gpu,
                                            self.grid.Ex_gpu,
                                            self.grid.Ey_gpu,
                                            self.grid.Ez_gpu,
                                            block=(1, 1, 1),
                                            grid=(round32(len(self.grid.hertziandipoles)), 1, 1))

        self.grid.iteration += 1

    def update_electric_b(self):
        """If there are any dispersive materials do 2nd part of dispersive
            update - it is split into two parts as it requires present and
            updated electric field values. Therefore it can only be completely
            updated after the electric field has been updated by the PML and
            source updates.
        """
        if config.materials['maxpoles'] != 0:
            self.dispersive_update_b(np.int32(self.grid.nx),
                                     np.int32(self.grid.ny),
                                     np.int32(self.grid.nz),
                                     np.int32(config.materials['maxpoles']),
                                     self.grid.updatecoeffsdispersive_gpu.gpudata,
                                     self.grid.Tx_gpu,
                                     self.grid.Ty_gpu,
                                     self.grid.Tz_gpu,
                                     self.grid.ID_gpu,
                                     self.grid.Ex_gpu,
                                     self.grid.Ey_gpu,
                                     self.grid.Ez_gpu,
                                     block=self.grid.tpb,
                                     grid=self.grid.bpg)

    def time_start(self):
        """Start event timers used to calculate solving time for model."""
        self.iterstart = self.drv.Event()
        self.iterend = self.drv.Event()
        self.iterstart.record()
        self.iterstart.synchronize()

    def calculate_tsolve(self):
        """Calculate solving time for model."""
        self.iterend.record()
        self.iterend.synchronize()
        tsolve = self.iterstart.time_till(self.iterend) * 1e-3

        return tsolve

    def finalise(self):
        """Copy data from GPU back to CPU to save to file(s)."""
        # Copy output from receivers array back to correct receiver objects
        if self.grid.rxs:
            gpu_get_rx_array(self.rxs_gpu.get(),
                             self.rxcoords_gpu.get(),
                             self.grid)

        # Copy data from any snapshots back to correct snapshot objects
        if self.grid.snapshots and not self.grid.snapsgpu2cpu:
            for i, snap in enumerate(self.grid.snapshots):
                gpu_get_snapshot_array(self.snapEx_gpu.get(),
                                       self.snapEy_gpu.get(),
                                       self.snapEz_gpu.get(),
                                       self.snapHx_gpu.get(),
                                       self.snapHy_gpu.get(),
                                       self.snapHz_gpu.get(),
                                       i,
                                       snap)

    def cleanup(self):
        """Cleanup GPU context."""
        # Remove context from top of stack and delete
        self.ctx.pop()
        del self.ctx