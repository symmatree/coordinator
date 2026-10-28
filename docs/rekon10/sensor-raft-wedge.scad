// Sensor raft pod wedge -- the angled camera support that bolts a Camera Module 3
// to one corner-mount pair of the rekon10 sensor raft.
//
// Spec: docs/rekon10/sensor-raft.md, "The wedge". Units: mm. ASCII only.
//
// Frame. The part is modelled as installed, in the raft frame: x = starboard,
// y = forward (the direction of travel, which is the line the two 1/4-20 holes lie
// on), z = up. The mating face -- the flat face that sits against the corner
// mount -- is z = 0 and the body hangs below it, so the camera plane faces down
// and out. The outer hole of the pair (the one nearer the raft corner) is on the
// z axis; the second hole is boss_pitch along y toward the raft centre. The spec
// text reads in the print frame (camera face up, mating face on the bed); set
// print_orientation = true for that, which is also the STL export orientation.
//
// Export:  openscad -o wedge-NE.stl -D 'corner="NE"' -D print_orientation=true \
//              -D show_camera=false -D show_bolts=false sensor-raft-wedge.scad
//
// Everything below the "derived" line follows from the parameters; the numbers
// the spec left open (plane depth, face width, insert pilot, tab lobe) are the
// parameters marked "not in spec".

$fn = 64;

// ---------------------------------------------------------------- which corner
// NE/NW are the front pair (compass north = forward), on the front boss pitch;
// SE/SW the rear pair. yaw is compass, positive east of north.
corner = "NE";                  // "NE" | "NW" | "SE" | "SW"

function corner_preset(c) =
    c == "NE" ? [  45, -1, 15.00] :
    c == "NW" ? [ -45, -1, 15.00] :
    c == "SE" ? [ 135, +1, 17.95] :
    c == "SW" ? [-135, +1, 17.95] : undef;

cam_yaw_deg      = corner_preset(corner)[0];   // facing, out from the corner
second_hole_sign = corner_preset(corner)[1];   // -1: second hole is aft of the outer one
boss_pitch       = corner_preset(corner)[2];   // centre to centre, from sensor-raft.md

// ---------------------------------------------------------------- the facing
cam_hfov_deg = 66;              // Camera Module 3 standard, landscape
cam_vfov_deg = 41;
cam_depression_deg = 90 - cam_vfov_deg / 2;   // 69.5: nadir at the bottom of the frame

// ---------------------------------------------------------------- the camera
cam_hole_dx = 21;               // M2 pattern, measured on the module (spec)
cam_hole_dy = 13;
cam_board_w = 25;               // CM3 standard PCB, preview only
cam_board_h = 24;
cam_board_t = 1.1;
cam_board_top_to_upper_hole = 2.0;  // upper holes sit this far below the board's top edge
m2_pilot_d = 3.1;               // not in spec: pilot for M2 heat-set inserts (3.2 OD)
m2_pilot_depth = 4.0;           // not in spec

// ---------------------------------------------------------------- the body
face_centre_depth = 14;         // not in spec: camera plane below the mating face, on the bolt axis
face_past_upper = 1.5;          // plane ends this far past the upper hole centres (spec)
face_past_lower = 1.5;          // not in spec: same margin past the lower holes
face_half_width = 14;           // not in spec: 12.5 board half-width + 1.5
trunc_normal_len = 6;           // from the plane's upper edge, normal to the plane, then vertical (spec)

// ---------------------------------------------------------------- the bolts
seat_d = 10;                    // flat seat for the bolt head (spec)
bolt_head_d = 9.5;              // 1/4-20 socket head cap screw
bolt_head_h = 6.35;
bolt_head_clear = 0.5;          // head to the back of the camera board
bolt_clear_d = 6.5;             // 1/4-20 close clearance (as oak-d-mount.scad)

tab_t = 4;                      // inboard tab thickness (spec)
tab_lobe_d = 16;                // not in spec: tab material around the second hole

// ---------------------------------------------------------------- display
show_camera = true;
show_frustum = true;
show_bolts = true;
print_orientation = false;

// ================================================================ derived
p = cam_depression_deg;
// Unit vectors in the raft frame.
hv = [sin(cam_yaw_deg), cos(cam_yaw_deg), 0];            // horizontal, out from the corner
rv = [cos(cam_yaw_deg), -sin(cam_yaw_deg), 0];           // image right
look = cos(p) * hv - [0, 0, sin(p)];                     // optical axis, down and out
up = sin(p) * hv + [0, 0, cos(p)];                       // image up, in the board plane
C = [0, 0, -face_centre_depth];                          // centre of the M2 pattern

// Section through the bolt axis in the (h, z) plane, h along hv.
// The plane rises going outboard: z = -face_centre_depth + h * cot(p).
u_max = cam_hole_dy / 2 + face_past_upper;
u_min = -(cam_hole_dy / 2 + face_past_lower);
function plane_pt(u) = [u * sin(p), -face_centre_depth + u * cos(p)];
E = plane_pt(u_max);                                     // upper edge of the plane
T = E + trunc_normal_len * [-cos(p), sin(p)];            // end of the normal drop
P_in = plane_pt(u_min);                                  // lower (inboard) edge of the plane
section_pts = [[P_in[0], 0], P_in, E, T, [T[0], 0]];

// Bolt seat: a horizontal floor, high enough that the head clears the plane
// everywhere under it.
plane_high_in_seat = -face_centre_depth + (seat_d / 2) / tan(p);
seat_floor_z = plane_high_in_seat + bolt_head_h + bolt_head_clear;

second_hole = [0, second_hole_sign * boss_pitch, 0];
second_hole_h = second_hole * hv;                        // in the body frame
second_hole_r = second_hole * rv;

echo(str("corner ", corner, ": yaw ", cam_yaw_deg, ", depression ", p, ", pitch ", boss_pitch));
echo(str("inboard face height ", -P_in[1], " mm; outboard vertical face height ", -T[1],
         " mm; outboard face ", T[0], " mm from the bolt axis"));
echo(str("seat floor ", -seat_floor_z, " mm below the mating face (material over the head); ",
         "seat wall to the outboard face ", T[0] - seat_d / 2, " mm at the floor"));
echo(str("upper insert pilot edge to plane edge ", face_past_upper - m2_pilot_d / 2, " mm"));

// ================================================================ frames
// Body frame: x = hv (out), y = -rv, z = up.
module in_body_frame() { rotate([0, 0, 90 - cam_yaw_deg]) children(); }

// Camera frame at the pattern centre: x = image right, y = image down, z = look.
module in_camera_frame() {
    dn = -up;
    multmatrix([[rv[0], dn[0], look[0], C[0]],
                [rv[1], dn[1], look[1], C[1]],
                [rv[2], dn[2], look[2], C[2]],
                [0, 0, 0, 1]])
        children();
}

module along(v) {   // orient +z along v
    n = v / norm(v);
    rotate([0, acos(n[2]), atan2(n[1], n[0])]) children();
}

// ================================================================ the part
module body() {
    in_body_frame()
        rotate([90, 0, 0])
            linear_extrude(height = 2 * face_half_width, center = true)
                polygon(section_pts);
}

module tab() {
    // Hull of a lobe round the second hole and a strip of the body's inboard face,
    // the strip as wide as the lobe and clipped to the body width.
    r_lo = max(-face_half_width, second_hole_r - tab_lobe_d / 2);
    r_hi = min( face_half_width, second_hole_r + tab_lobe_d / 2);
    hull() {
        translate(second_hole - [0, 0, tab_t]) cylinder(d = tab_lobe_d, h = tab_t);
        in_body_frame()
            translate([P_in[0], -r_hi, -tab_t]) cube([tab_t, r_hi - r_lo, tab_t]);
    }
}

module cutters() {
    // Seat: a vertical bore from below the plane up to the floor.
    translate([0, 0, P_in[1] - 1]) cylinder(d = seat_d, h = seat_floor_z - P_in[1] + 1);
    // 1/4-20 clearance, both holes.
    for (h = [[0, 0, 0], second_hole])
        translate(h + [0, 0, P_in[1] - 1]) cylinder(d = bolt_clear_d, h = -P_in[1] + 2);
    // M2 insert pilots, normal to the plane, into the body.
    in_camera_frame()
        for (sx = [-1, 1], sy = [-1, 1])
            translate([sx * cam_hole_dx / 2, sy * cam_hole_dy / 2, 0.01])
                mirror([0, 0, 1]) cylinder(d = m2_pilot_d, h = m2_pilot_depth + 0.01);
}

module wedge() {
    difference() {
        union() { body(); tab(); }
        cutters();
    }
}

// ================================================================ preview
module camera_preview() {
    in_camera_frame() {
        // Board: upper holes 2 mm below its top edge, connector end at image bottom.
        board_centre_y = cam_board_h / 2 - cam_board_top_to_upper_hole - cam_hole_dy / 2;
        color([0.10, 0.55, 0.10])
            translate([-cam_board_w / 2, board_centre_y - cam_board_h / 2, 0])
                cube([cam_board_w, cam_board_h, cam_board_t]);
        color([0.15, 0.15, 0.15, 0.6])
            translate([0, 0, cam_board_t]) cylinder(d = 14.5, h = 9);
        if (show_frustum) {
            depth = 60;
            color([0.15, 0.45, 1.0, 0.2])
                translate([0, 0, cam_board_t + 9])
                    hull() {
                        translate([0, 0, 0.5])
                            cube([2 * 0.5 * tan(cam_hfov_deg / 2), 2 * 0.5 * tan(cam_vfov_deg / 2), 0.01], center = true);
                        translate([0, 0, depth])
                            cube([2 * depth * tan(cam_hfov_deg / 2), 2 * depth * tan(cam_vfov_deg / 2), 0.01], center = true);
                    }
        }
    }
}

module bolt(head_top_z) {
    color([0.5, 0.5, 0.55]) {
        translate([0, 0, head_top_z - bolt_head_h]) cylinder(d = bolt_head_d, h = bolt_head_h);
        translate([0, 0, head_top_z]) cylinder(d = 6.35, h = -head_top_z + 12);
    }
}

module bolts_preview() {
    bolt(seat_floor_z);
    translate(second_hole) bolt(-tab_t);
}

module assembly() {
    wedge();
    if (show_camera) camera_preview();
    if (show_bolts) bolts_preview();
}

if (print_orientation) rotate([180, 0, 0]) wedge();
else assembly();
