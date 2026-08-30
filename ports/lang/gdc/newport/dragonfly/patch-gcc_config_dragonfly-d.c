gcc/ChangeLog:

2019-04-24  Iain Buclaw  <ibuclaw@gdcproject.org>

        * config.gcc (*-*-dragonfly*): Add dragonfly-d.o
        * config/dragonfly-d.c: New file.
        * config/t-dragonfly: New file.

libphobos/ChangeLog:

2019-04-24  Iain Buclaw  <ibuclaw@gdcproject.org>

        * configure.tgt: Add *-*-dragonfly* as a supported target.

diff --git gcc/config/dragonfly-d.c gcc/config/dragonfly-d.c
new file mode 100644
index 00000000000..b8d4a9ff45d
--- /dev/null
+++ gcc/config/dragonfly-d.c
@@ -0,0 +1,49 @@
+/* DragonFly support needed only by D front-end.
+   Copyright (C) 2019 Free Software Foundation, Inc.
+
+GCC is free software; you can redistribute it and/or modify it under
+the terms of the GNU General Public License as published by the Free
+Software Foundation; either version 3, or (at your option) any later
+version.
+
+GCC is distributed in the hope that it will be useful, but WITHOUT ANY
+WARRANTY; without even the implied warranty of MERCHANTABILITY or
+FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
+for more details.
+
+You should have received a copy of the GNU General Public License
+along with GCC; see the file COPYING3.  If not see
+<http://www.gnu.org/licenses/>.  */
+
+#include "config.h"
+#include "system.h"
+#include "coretypes.h"
+#include "tm_d.h"
+#include "d/d-target.h"
+#include "d/d-target-def.h"
+
+/* Implement TARGET_D_OS_VERSIONS for DragonFly targets.  */
+
+static void
+dragonfly_d_os_builtins (void)
+{
+  d_add_builtin_version ("DragonFlyBSD");
+  d_add_builtin_version ("Posix");
+}
+
+/* Implement TARGET_D_CRITSEC_SIZE for DragonFly targets.  */
+
+static unsigned
+dragonfly_d_critsec_size (void)
+{
+  /* This is the sizeof pthread_mutex_t, an opaque pointer.  */
+  return POINTER_SIZE_UNITS;
+}
+
+#undef TARGET_D_OS_VERSIONS
+#define TARGET_D_OS_VERSIONS dragonfly_d_os_builtins
+
+#undef TARGET_D_CRITSEC_SIZE
+#define TARGET_D_CRITSEC_SIZE dragonfly_d_critsec_size
+
+struct gcc_targetdm targetdm = TARGETDM_INITIALIZER;
