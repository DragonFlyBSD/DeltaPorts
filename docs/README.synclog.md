# Notes for each sync with freebsd-ports

## Feb 26th, 2019

**TODO for next sync**

- [x] Remove *LDFLAGS* workaround in some 'lang/rust' dependant ports like 'devel/rust-cbindgen' as the there is an upstream fix for this problem, see: https://svnweb.freebsd.org/ports?view=revision&revision=495724.

## May 6th 14:35:02 PDT 2019

- [x] Reverted print/texinfo fix to dports/master, it doesn't affect DeltaPorts.
- [x] Manually fixed multimedia/libva in `dports/master`, needs revert for the next sync.

## May 28 14:43:36 PDT 2019

- [x] Reverted multimedia/libva in `dports/master` before merging `dports/staged`
- [x] `lang/ghc` introduced a 'boostrap-package' target in the Makefile which collides with the MD one and the synth scan fails.

## Thu Jun 20 07:57:38 PDT 2019

Sync round done.

## Thu Jul 18 07:40:54 PDT 2019
- [ ] `devel/electron4` depends on `devel/chromium-gn` which synth can't build for any reason, fails in the scan phase with (FETCH) error but a `make fetch` in the port directory works.
- [ ] `science/py-GPy` was synced but its _BUILD_DEPENDS_ contains _${LOCALBASE}/lib/libomp.so:devel/openmp_ which our sync scripts mangled and only left _${LOCALBASE}/lib_. Needs investigation.
- [ ] `devel/gdb` has been locked because the new version (8.3) requires further porting.
- [x] `databases/influxdb` has been locked because the new version (1.7.6) requires further porting. UPDATE: Now unlocked and building.
- [ ] `www/node10` requires review.
