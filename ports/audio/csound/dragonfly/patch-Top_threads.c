--- Top/threads.c.orig
+++ Top/threads.c
@@ -1077,8 +1077,5 @@
 #include <stdbool.h>
 
 void csoundSpinLock(spin_lock_t *spinlock){
-    spin_lock_t unset = 0;
-    spin_lock_t set = 1;
-    while (!__atomic_compare_exchange_n(spinlock, &unset, set, false,
-                                        __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST)) { };
+    while (__atomic_test_and_set(spinlock, __ATOMIC_SEQ_CST)) { };
 }
