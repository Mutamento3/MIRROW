package com.mirrow.app;

import android.app.Activity;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.media.RingtoneManager;
import android.os.Build;
import android.os.Bundle;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.view.Gravity;
import android.view.Window;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * 查岗闹钟前台界面。通知负责系统级响铃/振动和锁屏唤醒，Activity 提供明确“按掉”动作。
 */
public class CheckupAlarmActivity extends Activity {
    private String eventId = "";
    private MediaPlayer mediaPlayer;
    private Vibrator vibrator;
    private boolean dismissed = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        eventId = getIntent().getStringExtra(CheckupAlarmController.EXTRA_EVENT_ID);
        if (eventId == null) eventId = "";

        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        }
        getWindow().addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                        | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                        | WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        String title = getIntent().getStringExtra(CheckupAlarmController.EXTRA_TITLE);
        String body = getIntent().getStringExtra(CheckupAlarmController.EXTRA_BODY);
        if (title == null || title.isEmpty()) title = "MIRROW查岗";
        if (body == null || body.isEmpty()) body = "查岗提醒";

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setPadding(64, 48, 64, 48);

        TextView titleView = new TextView(this);
        titleView.setText(title);
        titleView.setTextSize(28);
        titleView.setGravity(Gravity.CENTER);
        root.addView(titleView, new LinearLayout.LayoutParams(-1, -2));

        TextView bodyView = new TextView(this);
        bodyView.setText(body);
        bodyView.setTextSize(20);
        bodyView.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams bodyParams = new LinearLayout.LayoutParams(-1, -2);
        bodyParams.topMargin = 32;
        root.addView(bodyView, bodyParams);

        Button dismissButton = new Button(this);
        dismissButton.setText("按掉闹钟");
        dismissButton.setTextSize(20);
        dismissButton.setOnClickListener(v -> dismissAlarm());
        LinearLayout.LayoutParams buttonParams = new LinearLayout.LayoutParams(-1, -2);
        buttonParams.topMargin = 48;
        root.addView(dismissButton, buttonParams);
        setContentView(root);

        startAlarmEffects();
    }

    private void startAlarmEffects() {
        try {
            android.net.Uri uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
            mediaPlayer = MediaPlayer.create(this, uri);
            if (mediaPlayer != null) {
                mediaPlayer.setLooping(true);
                if (Build.VERSION.SDK_INT >= 21) {
                    mediaPlayer.setAudioAttributes(new AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_ALARM)
                            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                            .build());
                }
                mediaPlayer.start();
            }
        } catch (Exception ignored) {}

        try {
            vibrator = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
            if (vibrator != null && vibrator.hasVibrator()) {
                long[] pattern = new long[]{0, 600, 500, 600};
                if (Build.VERSION.SDK_INT >= 26) {
                    vibrator.vibrate(VibrationEffect.createWaveform(pattern, 0));
                } else {
                    vibrator.vibrate(pattern, 0);
                }
            }
        } catch (Exception ignored) {}
    }

    private void stopAlarmEffects() {
        try {
            if (mediaPlayer != null) {
                if (mediaPlayer.isPlaying()) mediaPlayer.stop();
                mediaPlayer.release();
                mediaPlayer = null;
            }
        } catch (Exception ignored) {}
        try { if (vibrator != null) vibrator.cancel(); } catch (Exception ignored) {}
    }

    private void dismissAlarm() {
        if (dismissed) return;
        dismissed = true;
        stopAlarmEffects();
        CheckupAlarmController.dismiss(this, eventId);
        finishAndRemoveTask();
    }

    @Override
    public void onBackPressed() {
        // 返回也视为用户明确按掉，避免闹钟界面被系统返回键卡住。
        dismissAlarm();
    }

    @Override
    protected void onDestroy() {
        stopAlarmEffects();
        super.onDestroy();
    }
}
