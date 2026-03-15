with Arthexis.ORM;

package body Apps.OCPP.Functions.OCPP_Functions is

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection) is
   begin
      Arthexis.ORM.Register_SQL_Function
        (Conn,
         Name => "ocpp_transaction_energy_wh",
         Arity => 2,
         SQL_Body =>
           "CREATE VIEW IF NOT EXISTS ocpp_transaction_energy AS "
           & "SELECT id, "
           & "CASE WHEN meter_stop_wh IS NULL THEN NULL "
           & "ELSE meter_stop_wh - meter_start_wh END AS energy_wh "
           & "FROM ocpp_transaction;");
   end Install;

end Apps.OCPP.Functions.OCPP_Functions;
