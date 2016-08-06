--- cfs.c.orig	2013-05-15 19:50:30.000000000 +0300
+++ cfs.c
@@ -49,7 +49,7 @@
 
 struct in_addr validhost;
 
-#if defined(SOLARIS2X) || defined(__NetBSD__) || defined(__FreeBSD__) || defined(__OpenBSD__)
+#if defined(SOLARIS2X) || defined(__NetBSD__) || defined(__FreeBSD__) || defined(__OpenBSD__) || defined(__DragonFly__)
 void nfs_program_2();
 void adm_program_2();
 #include <string.h>
@@ -247,7 +247,7 @@ initstuff(void)
 	setuid(0);
 	umask(0);
 
-#if defined(__NetBSD__) || defined(__bsdi__) || defined(__FreeBSD__) || defined(__OpenBSD__)
+#if defined(__NetBSD__) || defined(__bsdi__) || defined(__FreeBSD__) || defined(__OpenBSD__) || defined(__DrqagonFly__)
 #ifndef DEBUG
 	/* detach from terminal */
 	daemon(0,0);
