--- cargo-crates/glycin-utils-5.0.0-alpha/src/instruction_handler.rs.orig
+++ cargo-crates/glycin-utils-5.0.0-alpha/src/instruction_handler.rs
@@ -106,5 +106,6 @@
     }
 }
 
+#[cfg(target_os = "linux")]
 #[allow(non_camel_case_types)]
 extern "C" fn sigsys_handler(_: c_int, info: *mut siginfo_t, _: *mut c_void) {
@@ -140,4 +141,5 @@
     }
 }
 
+#[cfg(target_os = "linux")]
 fn setup_sigsys_handler() {
@@ -158,8 +160,12 @@
 
 #[allow(dead_code)]
+#[cfg(target_os = "linux")]
 pub extern "C" fn pre_main() {
     setup_sigsys_handler();
 }
 
+#[cfg(not(target_os = "linux"))]
+pub extern "C" fn pre_main() {}
+
 #[macro_export]
 macro_rules! init_main_loader {
