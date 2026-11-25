--- remote/node_modules/kerberos/src/unix/kerberos_gss.cc.orig
+++ remote/node_modules/kerberos/src/unix/kerberos_gss.cc
@@ -65,7 +65,7 @@
   DLOpenHandle& operator=(DLOpenHandle&) = delete;
 };
 
-#ifndef __FreeBSD__
+#if !defined(__FreeBSD__) && !defined(__DragonFly__)
 #define DYLIBS(V) \
     V(gssapi, "libgssapi_krb5.so.2") \
     V(krb5, "libkrb5.so.3") \
