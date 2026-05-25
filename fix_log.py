import pathlib

p = pathlib.Path(r'C:\Users\admin\.qclaw\workspace\rainyun-checkin\rainyun.py')
content = p.read_text(encoding='utf-8')

# Fix 1: Add logger.removeHandler before log_content = ...
old1 = '        # 2. 获取所有日志内容\n        log_content = log_capture_string.getvalue()'
new1 = '        # 2. 获取所有日志内容\n        try:\n            logger.removeHandler(log_capture_handler)\n        except Exception:\n            pass\n        log_content = log_capture_string.getvalue()'
if old1 in content:
    content = content.replace(old1, new1, 1)
    print('Fix 1 applied')
else:
    # Try CRLF version
    old1_crlf = old1.replace('\n', '\r\n')
    new1_crlf = new1.replace('\n', '\r\n')
    if old1_crlf in content:
        content = content.replace(old1_crlf, new1_crlf, 1)
        print('Fix 1 applied (CRLF)')
    else:
        print('Fix 1 FAILED')

# Fix 2: Fix the continue condition
old2 = '            if in_target or (not tag and "━━━━━━ 雨云签到 v" in line):\n                continue  # 跳过其他账号的头部行'
new2 = '            # 多账号：跳过非目标账号的行；单账号：跳过头部行\n            if (tag and not in_target) or (not tag and "━━━━━━ 雨云签到 v" in line):\n                continue'
if old2 in content:
    content = content.replace(old2, new2, 1)
    print('Fix 2 applied')
else:
    old2_crlf = old2.replace('\n', '\r\n')
    new2_crlf = new2.replace('\n', '\r\n')
    if old2_crlf in content:
        content = content.replace(old2_crlf, new2_crlf, 1)
        print('Fix 2 applied (CRLF)')
    else:
        print('Fix 2 FAILED')

# Fix 3: Move cleanup before return (remove dead code after return)
old3 = '        summary = "\\n".join(summary_lines) if summary_lines else "签到流程结束，详见日志"\n        # 不再逐账号发送，改为汇总后统一发送（由 run() 汇总）\n        return summary\n\n        # 4. 释放内存\n        log_capture_string.close()\n        if temp_dir and not debug:\n            shutil.rmtree(temp_dir, ignore_errors=True)'
new3 = '        summary = "\\n".join(summary_lines) if summary_lines else "签到流程结束，详见日志"\n        # 清理\n        try:\n            log_capture_string.close()\n        except Exception:\n            pass\n        if temp_dir and not debug:\n            shutil.rmtree(temp_dir, ignore_errors=True)\n        # 不再逐账号发送，改为汇总后统一发送（由 run() 汇总）\n        return summary'
if old3 in content:
    content = content.replace(old3, new3, 1)
    print('Fix 3 applied')
else:
    old3_crlf = old3.replace('\n', '\r\n')
    new3_crlf = new3.replace('\n', '\r\n')
    if old3_crlf in content:
        content = content.replace(old3_crlf, new3_crlf, 1)
        print('Fix 3 applied (CRLF)')
    else:
        print('Fix 3 FAILED')

p.write_text(content, encoding='utf-8', newline='')
print('All done, file written with original line endings preserved')
