import java.io.File;
import java.security.Permission;

/**
 * 第0步补充探针：在JDK 21上验证SecurityManager能否拦截RMLMapper错误路径的
 * System.exit。机制：SM的checkExit抛SecurityException时，System.exit的退出
 * 企图会以异常形式抛回调用方——从而把"不可捕获的进程死亡"变成"可捕获的异常"。
 * checkPermission覆写为全放行，本SM唯一职责是拦exit，不影响正常文件/网络访问。
 */
public class ExitGuardProbe {
    public static void main(String[] args) throws Exception {
        System.out.println("G0: 安装exit-guard...");
        try {
            System.setSecurityManager(new SecurityManager() {
                @Override public void checkExit(int status) {
                    throw new SecurityException("veto exit(" + status + ")");
                }
                @Override public void checkPermission(Permission perm) { /* 全放行 */ }
            });
            System.out.println("G1: guard安装成功");
        } catch (Throwable e) {
            System.out.println("G1-FAIL: guard安装失败: " + e);
            return;
        }

        String mapping = args[0];
        String outDir = args[1];
        new File(outDir).mkdirs();

        System.out.println("A: 调用Main.main(不存在的mapping，触发其内部System.exit)");
        try {
            be.ugent.rml.cli.Main.main(new String[]{
                    "-m", mapping + ".nonexistent", "-o", outDir + "/g.nq", "-s", "nquads"});
            System.out.println("B: 正常返回（未触发exit路径？）");
        } catch (Throwable e) {
            System.out.println("C: 捕获到 " + e.getClass().getSimpleName()
                    + " -> " + e.getMessage());
        }
        System.out.println("D: JVM仍然存活");

        System.out.println("E: 验证guard不影响正常映射...");
        long t0 = System.currentTimeMillis();
        be.ugent.rml.cli.Main.main(new String[]{
                "-m", mapping, "-o", outDir + "/guard_ok.nq", "-s", "nquads"});
        System.out.println("F: 合法mapping成功, "
                + (System.currentTimeMillis() - t0) + "ms, lines=" + count(outDir + "/guard_ok.nq"));
        System.out.println("G: PROBE-END");
    }

    static long count(String f) throws Exception {
        long c = 0;
        try (java.io.BufferedReader br = new java.io.BufferedReader(new java.io.FileReader(f))) {
            while (br.readLine() != null) c++;
        }
        return c;
    }
}
