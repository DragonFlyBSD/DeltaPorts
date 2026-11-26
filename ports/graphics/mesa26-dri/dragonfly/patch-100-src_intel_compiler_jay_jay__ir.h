--- src/intel/compiler/jay/jay_ir.h.orig
+++ src/intel/compiler/jay/jay_ir.h
@@ -579,7 +579,7 @@
    return jay_base_type(t) == JAY_TYPE_F || jay_base_type(t) == JAY_TYPE_BF;
 }
 
-enum jay_predication : uint8_t {
+enum PACKED jay_predication {
    /** No predication. */
    JAY_NOT_PREDICATED = 0,
 
