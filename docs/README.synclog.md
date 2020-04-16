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
- [x] `databases/influxdb` has been locked because the new version (1.7.6) requires further porting. UPDATE: Now unlocked and building.

## Wed Aug 28 03:20:16 PDT 2019

Sync round done

## Wed Sep  4 03:47:46 PDT 2019
- [X] `devel/gdb` has been unlocked and builds, but not tested well. The new version (8.3) requires further porting.
- [X] `www/node10` requires review.

## Sat Oct 26 01:19:14 PDT 2019
- [X] `devel/chromium-gn` build has been fixed.
- [X] `devel/openssl` remove additions.

## Sun Dec 22 15:31:02 UTC 2019
- [X] Fix OpenJDK11 bootstrap

## Thu Jan 23 15:40:10 UTC 2020
- [ ] `science/py-GPy` was synced but its _BUILD_DEPENDS_ contains _${LOCALBASE}/lib/libomp.so:devel/openmp_ which our sync scripts mangled and only left _${LOCALBASE}/lib_. Needs investigation.
- [ ] devel/rust-cbindgen is LOCK right now. We will try to keep it that way until DragonFly BSD does a new release.

## Thu Feb 13 23:09:54 2020
- [X] security/cargo-audit is LOCK right now. We will try to keep it that way until DragonFly BSD does a new release.
- [X] devel/rust-cbindgen is LOCK right now. We will try to keep it that way until DragonFly BSD does a new release.

## Fri Mar 13 22:23:52 2020
- [ ] `science/py-GPy` was synced but its _BUILD_DEPENDS_ contains _${LOCALBASE}/lib/libomp.so:devel/openmp_ which our sync scripts mangled and only left _${LOCALBASE}/lib_. Needs investigation.
- [ ] `x11-drivers/xf86-video-intel29` newport killed in favor of the port version (we have not found any reason to not use the ports one)

