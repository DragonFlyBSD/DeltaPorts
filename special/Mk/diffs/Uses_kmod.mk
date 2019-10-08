--- Uses/kmod.mk.orig	2017-02-14 06:57:39 UTC
+++ Uses/kmod.mk
@@ -29,9 +29,9 @@ CATEGORIES+=	kld
 
 SSP_UNSAFE=	kernel module supports SSP natively
 
-KMODDIR?=	/boot/modules
+KMODDIR?=	/boot/modules.local
 .if ${KMODDIR} == /boot/kernel
-KMODDIR=	/boot/modules
+KMODDIR=	/boot/modules.local
 .endif
 
 _DEBUG_KMOD_SH= \
