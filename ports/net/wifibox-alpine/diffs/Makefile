--- Makefile.orig	2022-09-05 22:18:40 UTC
+++ Makefile
@@ -137,7 +137,7 @@ _IPW2200_FIRMWARE=	iwi/ipw2200-bss \
 			iwi/ipw2200-ibss
 _MT76_FIRMWARE=		b6e865e2cc7080c91ec34a9dd3648d25f7ce04c6
 
-_LINUX64_KMOD!=		(kldstat -qn linux64 && echo "found") || echo ""
+_LINUX64_KMOD=
 
 .if empty(_LINUX64_KMOD)
 IGNORE=	needs the linux64 kernel module to build
