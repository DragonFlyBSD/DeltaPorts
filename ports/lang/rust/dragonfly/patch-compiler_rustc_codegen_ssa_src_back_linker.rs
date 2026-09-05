--- compiler/rustc_codegen_ssa/src/back/linker.rs	2025-09-14 23:05:11.000000000 +0800
+++ compiler/rustc_codegen_ssa/src/back/linker.rs	2026-07-02 00:54:41.892519000 +0800
@@ -864,7 +864,10 @@
             } else {
                 let mut arg = OsString::from("--version-script=");
                 arg.push(path);
-                self.link_arg(arg).link_arg("--no-undefined-version");
+                self.link_arg(arg);
+                if self.sess.target.os != Os::Dragonfly {
+                    self.link_arg("--no-undefined-version");
+                }
             }
         }
     }
