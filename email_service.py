"""
SmartFace Email Notification Service
======================================
- Professional HTML email template
- HR CC option (configurable)
- Called by APScheduler or manually
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from config import Config
from database.db import get_db_connection, get_setting


def get_email_html(employee_name, date_str, company_name):
    """Generate professional HTML email."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="margin:0; padding:0; background:#f0f2f5; font-family:'Segoe UI',Arial,sans-serif;">
        <table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5; padding:40px 20px;">
            <tr>
                <td align="center">
                    <table width="560" cellpadding="0" cellspacing="0" style="background:#ffffff; border-radius:16px; overflow:hidden; box-shadow:0 4px 24px rgba(0,0,0,0.08);">
                        <!-- HEADER -->
                        <tr>
                            <td style="background:linear-gradient(135deg,#0a0e1a,#1a1f2e); padding:32px 40px; text-align:center;">
                                <div style="font-size:24px; font-weight:800; color:#ffffff; letter-spacing:0.5px;">
                                    🛡️ SmartFace AI
                                </div>
                                <div style="font-size:12px; color:#10b981; font-weight:600; letter-spacing:2px; text-transform:uppercase; margin-top:4px;">
                                    {company_name}
                                </div>
                            </td>
                        </tr>

                        <!-- BODY -->
                        <tr>
                            <td style="padding:40px;">
                                <div style="font-size:14px; color:#6b7280; margin-bottom:8px;">Attendance Alert</div>
                                <div style="font-size:22px; font-weight:700; color:#1f2937; margin-bottom:24px;">
                                    Absence Detected
                                </div>

                                <p style="font-size:15px; color:#374151; line-height:1.7; margin-bottom:20px;">
                                    Dear <strong>{employee_name}</strong>,
                                </p>

                                <p style="font-size:15px; color:#374151; line-height:1.7; margin-bottom:20px;">
                                    Our Smart Attendance system has detected that <strong>no attendance was recorded</strong> for you on <strong>{date_str}</strong>.
                                </p>

                                <div style="background:#fef3c7; border-left:4px solid #f59e0b; padding:16px 20px; border-radius:0 8px 8px 0; margin-bottom:24px;">
                                    <div style="font-size:13px; font-weight:700; color:#92400e; margin-bottom:4px;">⚠️ Action Required</div>
                                    <div style="font-size:13px; color:#78350f; line-height:1.6;">
                                        If you were present, please contact HR to update your record. If you were on approved leave, no action is needed.
                                    </div>
                                </div>

                                <p style="font-size:14px; color:#6b7280; line-height:1.7;">
                                    This is an automated notification from the SmartFace AI Attendance system. Please do not reply to this email.
                                </p>
                            </td>
                        </tr>

                        <!-- FOOTER -->
                        <tr>
                            <td style="background:#f9fafb; padding:24px 40px; border-top:1px solid #e5e7eb; text-align:center;">
                                <div style="font-size:12px; color:#9ca3af;">
                                    Powered by <strong style="color:#10b981;">SmartFace AI</strong> • {company_name}
                                </div>
                                <div style="font-size:11px; color:#d1d5db; margin-top:4px;">
                                    Sent on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}
                                </div>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """


def send_absentee_emails():
    """Detect absentees and send email notifications with optional HR CC."""
    conn = get_db_connection()
    today = datetime.now().strftime("%Y-%m-%d")

    # Get company name from settings
    company_name = get_setting('company_name', 'Sofzenix Technologies')
    hr_email = get_setting('hr_email', '')

    # Get all active employees
    employees = conn.execute("SELECT id, name, email FROM users WHERE role='employee'").fetchall()

    # Get all users who marked attendance today
    present_rows = conn.execute("SELECT DISTINCT user_id FROM attendance WHERE date=?", (today,)).fetchall()
    present_user_ids = [row['user_id'] for row in present_rows]

    absent_users = [emp for emp in employees if emp['id'] not in present_user_ids]

    if not absent_users:
        print(f"[{datetime.now()}] No absentees today.")
        conn.close()
        return

    # Check if SMTP credentials exist
    if not Config.MAIL_USERNAME or not Config.MAIL_PASSWORD:
        print(f"[{datetime.now()}] Mail config not set. Absentees detected:")
        for u in absent_users:
            print(f"  - {u['name']} ({u['email']})")
        conn.close()
        return

    try:
        server = smtplib.SMTP(Config.MAIL_SERVER, Config.MAIL_PORT)
        server.starttls()
        server.login(Config.MAIL_USERNAME, Config.MAIL_PASSWORD)
    except Exception as e:
        print(f"[{datetime.now()}] SMTP connection failed: {e}")
        conn.close()
        return

    sent_count = 0
    for user in absent_users:
        msg = MIMEMultipart('alternative')
        msg['From'] = Config.MAIL_DEFAULT_SENDER
        msg['To'] = user['email']
        msg['Subject'] = f"SmartFace: Attendance Not Recorded — {today}"

        # Add HR CC if configured
        if hr_email and hr_email.strip():
            msg['Cc'] = hr_email.strip()

        # Plain text fallback
        plain_body = f"""Dear {user['name']},

Our Smart Attendance system detected that no attendance was recorded for you on {today}.

If you were present, please contact HR to update your record.
If you were on approved leave, no action is needed.

Best regards,
SmartFace Admin — {company_name}
"""
        # HTML body
        html_body = get_email_html(user['name'], today, company_name)

        msg.attach(MIMEText(plain_body, 'plain'))
        msg.attach(MIMEText(html_body, 'html'))

        try:
            recipients = [user['email']]
            if hr_email and hr_email.strip():
                recipients.append(hr_email.strip())
            server.sendmail(Config.MAIL_DEFAULT_SENDER, recipients, msg.as_string())
            sent_count += 1
            print(f"  ✓ Sent to {user['email']}")
        except Exception as e:
            print(f"  ✗ Failed: {user['email']} — {e}")

    server.quit()
    conn.close()
    print(f"[{datetime.now()}] Absentee emails sent: {sent_count}/{len(absent_users)}")


if __name__ == "__main__":
    send_absentee_emails()
