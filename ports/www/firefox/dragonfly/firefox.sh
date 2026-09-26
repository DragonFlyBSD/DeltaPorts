#!/bin/sh
# DragonFly rtld workaround for firefox.
#
# On DragonFly a runtime dlopen + symbol bind serializes on the rtld global
# lock. Under the concurrent load of a multiprocess browser this freezes the
# parent UI thread ~30s the first time a heavy page (mass first-use PLT binds)
# or a video (lazy dlopen of the codec libraries) triggers it -- every tab
# stalls until it clears. Two mitigations, both applied before exec:
#   * LD_BIND_NOW=1 makes rtld resolve all symbols eagerly at startup, single
#     threaded, so no lazy PLT binding happens mid-session. DragonFly rtld
#     ignores the DT_BIND_NOW ELF flag firefox already links, so the
#     environment variable is the only thing that takes effect.
#   * Preload the media codec libraries (libvpx, dav1d) so firefox does not
#     dlopen them lazily when the first video plays. The glob tracks whatever
#     SONAME major is installed and degrades to no-preload if none is found.
export LD_BIND_NOW=1
_preload=
for _lib in /usr/local/lib/libvpx.so.[0-9] /usr/local/lib/libvpx.so.[0-9][0-9] \
            /usr/local/lib/libdav1d.so.[0-9] /usr/local/lib/libdav1d.so.[0-9][0-9]; do
	[ -e "$_lib" ] && _preload="${_preload:+$_preload:}$_lib"
done
[ -n "$_preload" ] && export LD_PRELOAD="$_preload${LD_PRELOAD:+:$LD_PRELOAD}"
exec /usr/local/lib/firefox/firefox "$@"
