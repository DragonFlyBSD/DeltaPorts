--- src/llvm-project/llvm/lib/Transforms/Vectorize/VPlanUtils.cpp.orig
+++ src/llvm-project/llvm/lib/Transforms/Vectorize/VPlanUtils.cpp
@@ -31,6 +31,7 @@
     return false;
   unsigned Opcode = VPI->getOpcode();
   switch (Opcode) {
+  case Instruction::ExtractValue:
   case Instruction::FCmp:
   case Instruction::ICmp:
   case Instruction::Select:
