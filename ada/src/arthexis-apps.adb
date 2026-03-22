with Apps.Core.Functions.Core_Functions;
with Apps.Core.Models.Core_Models;
with Apps.Core.Triggers.Core_Triggers;
with Apps.OCPP.Functions.OCPP_Functions;
with Apps.OCPP.Models.OCPP_Models;
with Apps.OCPP.Triggers.OCPP_Triggers;
with Arthexis.ORM;

package body Arthexis.Apps is

   procedure Install_All (Conn : in out Arthexis.ORM.Database_Connection) is
      In_Transaction : Boolean := False;
   begin
      Arthexis.ORM.Execute (Conn, "BEGIN IMMEDIATE;");
      In_Transaction := True;

      Apps.Core.Models.Core_Models.Install (Conn);
      Apps.Core.Functions.Core_Functions.Install (Conn);
      Apps.Core.Triggers.Core_Triggers.Install (Conn);

      Apps.OCPP.Models.OCPP_Models.Install (Conn);
      Apps.OCPP.Functions.OCPP_Functions.Install (Conn);
      Apps.OCPP.Triggers.OCPP_Triggers.Install (Conn);

      Arthexis.ORM.Execute (Conn, "COMMIT;");
      In_Transaction := False;
   exception
      when others =>
         if In_Transaction then
            Arthexis.ORM.Execute (Conn, "ROLLBACK;");
         end if;
         raise;
   end Install_All;

end Arthexis.Apps;
