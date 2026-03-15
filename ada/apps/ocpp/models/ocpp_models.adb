with Arthexis.ORM;

package body Apps.OCPP.Models.OCPP_Models is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS ocpp_charge_point ("
         & "id INTEGER PRIMARY KEY, "
         & "serial_number TEXT NOT NULL UNIQUE, "
         & "last_seen_utc TEXT, "
         & "status TEXT NOT NULL DEFAULT 'unknown'"
         & ");");

      Arthexis.ORM.Execute
        (Conn,
         "CREATE TABLE IF NOT EXISTS ocpp_transaction ("
         & "id INTEGER PRIMARY KEY, "
         & "charge_point_id INTEGER NOT NULL, "
         & "connector_id INTEGER NOT NULL, "
         & "meter_start_wh INTEGER NOT NULL DEFAULT 0, "
         & "meter_stop_wh INTEGER, "
         & "started_at_utc TEXT NOT NULL, "
         & "ended_at_utc TEXT, "
         & "FOREIGN KEY (charge_point_id) REFERENCES ocpp_charge_point(id)"
         & ");");
   end Install;

end Apps.OCPP.Models.OCPP_Models;
