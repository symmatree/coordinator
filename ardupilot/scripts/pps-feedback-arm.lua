-- pps-feedback-arm.lua -- arm the CAM1 feedback pin once per boot.
--
-- ArduCopter 4.7.2 installs the CAM1_FEEDBAK_PIN interrupt inside
-- AP_Camera_Backend::take_picture() (libraries/AP_Camera/AP_Camera_Backend.cpp:141)
-- and nowhere else. Until something takes a picture the PPS edges on S11 are not
-- sampled and no CAM rows are logged. This script takes one picture a few seconds
-- after boot. With CAM1_TYPE=5 (MAVLink) that sends one DIGICAM_CONTROL to the GCS
-- and writes one TRIG row; every PPS edge after that logs as a CAM row.
--
-- Lives on the FC's SD card as APM/scripts/pps-feedback-arm.lua. SCR_ENABLE is 1
-- (ardupilot/inputs/logging-notify-misc.param). Scripts load at boot.

local MAV_SEVERITY_INFO = 6
local CAM1 = 0

local function arm()
  camera:take_picture(CAM1)
  gcs:send_text(MAV_SEVERITY_INFO, "PPS: CAM1 feedback armed")
  -- no return: the script is done for this boot
end

return arm, 5000
