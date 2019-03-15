# Notes for each sync with freebsd-ports

## Feb 26th, 2019

- XXX - GNUstep programs and libraries will fail to link with lld, *LLD_UNSAFE=yes* is then used via `Mk/Uses/gnustep.mk`.


**TODO for next sync**

- [ ] Remove *LLD_UNSAFE=yes* from *Mk/Uses/gnustep.mk* in favour of *OBJC_LLD= gold* in Mk/Uses/objc.mk, as pointed out by zrj. This should remove the 'devel/binutils' dependency imposed by LLD_UNSAFE usage.
- [ ] Remove *LDFLAGS* workaround in some 'lang/rust' dependant ports like 'devel/rust-cbindgen' as the there is an upstream fix for this problem, see: https://svnweb.freebsd.org/ports?view=revision&revision=495724


