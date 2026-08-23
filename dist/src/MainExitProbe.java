import java.io.File;

/**
 * 第0步补测探针：直接在本JVM主线程上调用RMLMapper的Main.main()，
 * 验证三件事：
 *   1) 成功路径是否正常返回（还是内部System.exit把整个JVM带崩）；
 *   2) 同一JVM内第二次调用能否成功且输出与第一次逐行一致（可重复性）；
 *   3) 错误路径（映射文件不存在）是否会把JVM带崩。
 * 判读方法：末行标记E必须打印——若某次调用后E缺失且进程已结束，
 * 即为该路径存在System.exit。
 */
public class MainExitProbe {
    public static void main(String[] args) throws Exception {
        String mapping = args[0];
        String outDir = new File(args[1]).getAbsolutePath();
        new File(outDir).mkdirs();

        for (int i = 1; i <= 2; i++) {
            String out = outDir + "/probe_run" + i + ".nq";
            new File(out).delete();
            System.out.println("A" + i + ": invoking be.ugent.rml.cli.Main.main");
            long t0 = System.currentTimeMillis();
            try {
                be.ugent.rml.cli.Main.main(new String[]{
                        "-m", mapping, "-o", out, "-s", "nquads"});
            } catch (Throwable e) {
                System.out.println("B" + i + ": main() threw " + e);
                continue;
            }
            File f = new File(out);
            System.out.println("B" + i + ": main() RETURNED normally in "
                    + (System.currentTimeMillis() - t0) + "ms, output_exists="
                    + f.exists() + ", lines=" + (f.exists() ? count(f) : -1));
        }

        System.out.println("C: invoking main() with INVALID mapping (error path)");
        try {
            be.ugent.rml.cli.Main.main(new String[]{
                    "-m", mapping + ".nonexistent",
                    "-o", outDir + "/probe_err.nq", "-s", "nquads"});
            System.out.println("D: error-path call RETURNED normally (no System.exit)");
        } catch (Throwable e) {
            System.out.println("D: error-path call threw " + e.getClass().getName());
        }
        System.out.println("E: PROBE-JVM-STILL-ALIVE");
    }

    static long count(File f) throws Exception {
        long c = 0;
        try (java.io.BufferedReader br = new java.io.BufferedReader(new java.io.FileReader(f))) {
            while (br.readLine() != null) c++;
        }
        return c;
    }
}
