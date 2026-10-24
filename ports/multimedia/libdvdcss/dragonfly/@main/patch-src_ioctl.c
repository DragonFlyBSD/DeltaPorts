--- src/ioctl.c.orig
+++ src/ioctl.c
@@ -1122,6 +1122,9 @@
     if (i_ret == 0)
         memcpy(p_data_buffer, p_buffer, CPRM_MEDIA_ID_SIZE);
 
+#elif defined( __DragonFly__ )
+    /* No SCSI pass-through for the CPRM commands. */
+    i_ret = -1;
 #else
 #   error "DVD ioctls are unavailable on this system"
 
@@ -1271,6 +1274,9 @@
         memcpy(p_mkb_pack, p_buffer + 4, CPRM_MKB_PACK_SIZE);
     }
 
+#elif defined( __DragonFly__ )
+    /* No SCSI pass-through for the CPRM commands. */
+    i_ret = -1;
 #else
 #   error "DVD ioctls are unavailable on this system"
 
