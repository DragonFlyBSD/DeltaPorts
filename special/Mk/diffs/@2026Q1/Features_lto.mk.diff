--- Features/lto.mk.orig	2024-09-10 23:29:04 UTC
+++ Features/lto.mk
@@ -10,7 +10,12 @@ LTO_Include_MAINTAINER=	pkubaj@FreeBSD.o
 .  if !defined(LTO_UNSAFE) || defined(LTO_DISABLE_CHECK)
 .    if "${ARCH}" == "riscv64" && !defined(LTO_DISABLE_CHECK)
        DEV_WARNING+=	"LTO is currently broken on riscv64, to override set LTO_DISABLE_CHECK=yes"
-.    elif defined(_INCLUDE_USES_CARGO_MK)
+.    elif defined(_INCLUDE_USES_CARGO_MK) && "${OPSYS}" != "DragonFly"
+#
+# XXX: We cannot enable LTO optimizations here for rust because it ignores
+#      libraries at link time (possibly due to --as-needed being specified).
+#      This is likely a bug and we have to sort it out before this can be
+#      enabled.
    CARGO_ENV+=	CARGO_PROFILE_RELEASE_LTO="true" \
 		CARGO_PROFILE_RELEASE_PANIC="abort" \
 		CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1
