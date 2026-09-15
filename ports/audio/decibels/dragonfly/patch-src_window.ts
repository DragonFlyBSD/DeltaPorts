--- src/window.ts.orig
+++ src/window.ts
@@ -16,2 +16,7 @@
 Gio._promisify(Gtk.FileDialog.prototype, "open", "open_finish");
 Gio._promisify(Gio.File.prototype, "query_info_async", "query_info_finish");
+Gio._promisify(
+  Gio.File.prototype,
+  "mount_enclosing_volume",
+  "mount_enclosing_volume_finish",
+);
@@ -163,3 +163,30 @@
   load_uri(uri: string) {
     this.stream.set_uri(uri);
   }
+
+  private async mount_smb_file(file: Gio.File): Promise<void> {
+    if (file.get_uri_scheme() !== "smb") return;
+
+    const mount_operation = new Gio.MountOperation();
+    mount_operation.connect(
+      "ask-password",
+      (
+        operation: Gio.MountOperation,
+        _message: string,
+        _default_user: string | null,
+        _default_domain: string | null,
+        flags: Gio.AskPasswordFlags,
+      ) => {
+        if (flags & Gio.AskPasswordFlags.ANONYMOUS_SUPPORTED) {
+          operation.set_anonymous(true);
+        }
+        operation.reply(Gio.MountOperationResult.HANDLED);
+      },
+    );
+
+    await (file.mount_enclosing_volume as unknown as (
+      flags: Gio.MountMountFlags,
+      operation: Gio.MountOperation,
+      cancellable: Gio.Cancellable | null,
+    ) => Promise<boolean>)(Gio.MountMountFlags.NONE, mount_operation, null);
+  }
@@ -167,3 +194,14 @@
   async load_file(file: Gio.File, autoplay = true) {
+    try {
+      await this.mount_smb_file(file);
+    } catch (error) {
+      if (
+        !(error instanceof GLib.Error) ||
+        error.code !== Gio.IOErrorEnum.ALREADY_MOUNTED
+      ) {
+        throw error;
+      }
+    }
+
     const fileInfo = await file
       .query_info_async(
