--- src/Model.h.orig
+++ src/Model.h
@@ -103,6 +103,8 @@
 _PACK_ATTR;
 
 
+/* Not packed: gcc rejects references to packed fields (BinSumm). */
+#pragma pack()
 struct PPM_MODEL
 {
   struct PPM_CONTEXT *MaxContext;
@@ -161,7 +163,8 @@
 
  
 }
-_PACK_ATTR;
+;
+#pragma pack(1)
 
 struct PPM_ENCODER : PPM_MODEL {
   FILE* outfile;
