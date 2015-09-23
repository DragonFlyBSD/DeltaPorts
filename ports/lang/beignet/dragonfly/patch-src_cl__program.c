--- src/cl_program.c.orig	2015-07-02 10:39:05.000000000 +0300
+++ src/cl_program.c
@@ -166,7 +166,8 @@ error:
   return err;
 }
 
-inline cl_bool isBitcodeWrapper(const unsigned char *BufPtr, const unsigned char *BufEnd)
+static inline cl_bool 
+isBitcodeWrapper(const unsigned char *BufPtr, const unsigned char *BufEnd)
 {
   // See if you can find the hidden message in the magic bytes :-).
   // (Hint: it's a little-endian encoding.)
@@ -177,7 +178,8 @@ inline cl_bool isBitcodeWrapper(const un
     BufPtr[3] == 0x0B;
 }
 
-inline cl_bool isRawBitcode(const unsigned char *BufPtr, const unsigned char *BufEnd)
+static inline cl_bool 
+isRawBitcode(const unsigned char *BufPtr, const unsigned char *BufEnd)
 {
   // These bytes sort of have a hidden message, but it's not in
   // little-endian this time, and it's a little redundant.
@@ -744,10 +746,16 @@ cl_program_compile(cl_program
     p->opaque = compiler_program_compile_from_source(p->ctx->device->vendor_id, p->source, temp_header_path,
         p->build_log_max_sz, options, p->build_log, &p->build_log_sz);
 
+#ifndef __DragonFly__
     char rm_path[255]="rm ";
     strncat(rm_path, temp_header_path, strlen(temp_header_path));
     strncat(rm_path, " -rf", 4);
     int temp = system(rm_path);
+#else
+    char rm_path[255]="rm -rf ";
+    strncat(rm_path, temp_header_path, strlen(temp_header_path));
+    int temp = system(rm_path);
+#endif
 
     if(temp){
       assert(0);
