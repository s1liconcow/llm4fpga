# Third-party material in the macro package

The physical views and executable simulation cell models use the SkyWater
`sky130_fd_sc_hd` standard-cell library.
Copyright 2020 The SkyWater PDK Authors. Its source headers and
[upstream license](https://github.com/google/skywater-pdk-libs-sky130_fd_sc_hd/blob/main/LICENSE)
specify Apache-2.0. The package includes that license text as `LICENSE.skywater`.
The merged GDS contains library cell geometry together with the generated gyro
floorplan, placement, wiring, and power grid. The pinned PDK revision is recorded
in the package provenance.
The replay models are generated from the library's Boolean and flip-flop
definitions; verified physical-only instances are removed from that simulation
copy. The delivered physical netlists retain those instances.

The custom physical-flow extensions use LibreLane's Apache-2.0 interfaces and
script conventions. The package also includes the Apache-2.0 text as
`LICENSE.librelane`; the custom Tcl repair files carry SPDX notices.

These notices describe the included third-party material. They do not assert
ownership of the user-supplied algorithm report or image, which are identified
by title and hashes in the design documentation and are not redistributed here.
