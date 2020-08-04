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
- [X] `x11-drivers/xf86-video-intel29` newport killed in favor of the port version (we have not found any reason to not use the ports one)

## Thu Apr 16 08:58:19 2020 +0000

### Merge rejects:
* deskutils/calibre: 1 out of 1 hunks failed--saving rejects to pkg-plist.rej
* devel/gcc-msp430-ti-toolchain: 2 out of 2 hunks failed--saving rejects to Makefile.rej
* lang/pypy: 1 out of 2 hunks failed--saving rejects to Makefile.rej
* lang/python37: 2 out of 4 hunks failed--saving rejects to Makefile.rej
* mail/spamd: 1 out of 1 hunks failed--saving rejects to files/pkg-message.in.rej
* net/ipxe: 1 out of 1 hunks failed--saving rejects to Makefile.rej
* net/ntopng: 1 out of 1 hunks failed--saving rejects to Makefile.rej
* sysutils/slurm-wlm: 1 out of 1 hunks failed--saving rejects to pkg-plist.rej
* www/mod_rivet: 1 out of 1 hunks failed--saving rejects to Makefile.rej

### Tasks

- [X] `www/chromium` to be updated (81.0.4044.92)
- [X] `x11-drivers/xf86-video-ati` newport removal in favor of the ports version.
- [X] `lang/zig` should build fine now.

## Wed May 13 16:21:49 2020 +0000

### Merge rejects:

* devel/elfutils: No such line 56 in input file, ignoring 2 out of 3 hunks failed--saving rejects to pkg-plist.rej
* lang/rust: 1 out of 1 hunks failed--saving rejects to distinfo.rej
* math/blis: 1 out of 1 hunks failed--saving rejects to Makefile.rej
* net/asterisk13: 1 out of 1 hunks failed--saving rejects to Makefile.rej
* security/doas: 1 out of 1 hunks failed--saving rejects to Makefile.rej

### Tasks
- [X] `lang/rust` to be updated (1.43.1)

## Sat Jun 13 17:14:49 2020 +0000 ([440656f57b29d9](https://github.com/freebsd/freebsd-ports/commit/440656f57b29d9))

## Merge rejects:
* graphics/dspdfviewer: 1 out of 1 hunks failed--saving rejects to Makefile.rej
* rm: /tmp/merge.workarea/files/patch-configure: No such file or directory
* graphics/mesa-dri: 1 out of 1 hunks failed--saving rejects to Makefile.rej
* graphics/mesa-dri: No such line 113 in input file, ignoring 1 out of 3 hunks failed--saving rejects to Makefile.common.rej
* graphics/mesa-libs: 1 out of 1 hunks failed--saving rejects to Makefile.rej
* java/openjdk7: 1 out of 1 hunks failed--saving rejects to distinfo.rej
* lang/ghc: 1 out of 5 hunks failed--saving rejects to Makefile.rej
* lang/ghc: No file to patch. Skipping... 1 out of 1 hunks ignored--saving rejects to bsd.cabal.mk.rej
* lang/rust: 1 out of 1 hunks failed--saving rejects to distinfo.rej
* net/asterisk13: 1 out of 2 hunks failed--saving rejects to Makefile.rej
* net/ipxe: 1 out of 1 hunks failed--saving rejects to Makefile.rej
* science/xcrysden: 1 out of 1 hunks failed--saving rejects to Makefile.rej
* sysutils/etc_os-release: 1 out of 1 hunks failed--saving rejects to Makefile.rej

### Tasks
- [X] `lang/rust` to be updated (1.44.0)
- [X] `www/chromium` to be updated (83.0.4103.97)

## Tue Jul 28 19:46:27 2020 +0000 ([bb235a4af795f5](https://github.com/freebsd/freebsd-ports/commit/bb235a4af795f5))

## Merge rejects:
* deskutils/calibre: 1 out of 1 hunks failed--saving rejects to pkg-plist.rej
* devel/aarch64-none-elf-gcc: 3 out of 3 hunks failed--saving rejects to pkg-plist.rej
* devel/arm-none-eabi-gcc: 3 out of 3 hunks failed--saving rejects to pkg-plist.rej
* devel/libccid: No file to patch. Skipping... 1 out of 1 hunks ignored--saving rejects to pkg-plist.rej
* devel/libvirt: No such line 325 in input file, ignoring
* devel/riscv32-unknown-elf-gcc: 1 out of 1 hunks failed--saving rejects to pkg-plist.rej
* devel/riscv64-none-elf-gcc: 1 out of 1 hunks failed--saving rejects to pkg-plist.rej
* graphics/dspdfviewer: 1 out of 1 hunks failed--saving rejects to Makefile.rej
* graphics/mesa-dri: 1 out of 2 hunks failed--saving rejects to Makefile.rej
* lang/rust: 1 out of 1 hunks failed--saving rejects to distinfo.rej
* math/gh-bc: 1 out of 2 hunks failed--saving rejects to pkg-plist.rej
* math/saga: 1 out of 3 hunks failed--saving rejects to Makefile.rej
* multimedia/kodi: No such line 2422 in input file, ignoring 1 out of 2 hunks failed--saving rejects to pkg-plist.rej
* print/lilypond-devel: 1 out of 1 hunks failed--saving rejects to Makefile.rej
* security/clamav: 1 out of 1 hunks failed--saving rejects to Makefile.rej

### Tasks
- [ ] `audio/libmysofa` should use base compiler (mpfr, gmp updates to be checked)
- [ ] `graphics/exiv2` should use base compiler (mpfr, gmp updates to be checked)
- [ ] `lang/rust` to be updated (1.45.0)
- [ ] `java/openjdk8` to be updated (8.252.09)
- [ ] `science/py-GPy` was synced but its _BUILD_DEPENDS_ contains _${LOCALBASE}/lib/libomp.so:devel/openmp_ which our sync scripts mangled and only left _${LOCALBASE}/lib_. Needs investigation.
- [ ] Mesa update to update to 20.0.4 **1)NOT YET** **2)Mesa-19 already in ports**

