with Arthexis.ORM;

package body Apps.OCPP.Triggers.OCPP_Triggers is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Register_Trigger
        (Conn,
         Name => "ocpp_transaction_meter_guard",
         SQL_Body =>
           "CREATE TRIGGER IF NOT EXISTS ocpp_transaction_meter_guard "
           & "BEFORE UPDATE ON ocpp_transaction "
           & "WHEN NEW.meter_stop_wh IS NOT NULL "
           & "AND NEW.meter_stop_wh < NEW.meter_start_wh "
           & "BEGIN "
           & "SELECT RAISE(FAIL, 'meter_stop_wh must be >= meter_start_wh'); "
           & "END;");
   end Install;

end Apps.OCPP.Triggers.OCPP_Triggers;
