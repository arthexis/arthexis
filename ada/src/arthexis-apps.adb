with Apps.App.Functions.App_Functions;
with Apps.App.Models.App_Models;
with Apps.Command.Functions.Command_Functions;
with Apps.Command.Models.Command_Models;
with Apps.Core.Functions.Core_Functions;
with Apps.Core.Models.Core_Models;
with Apps.Core.Triggers.Core_Triggers;
with Apps.Fixtures.Functions.Fixtures_Functions;
with Apps.Fixtures.Models.Fixtures_Models;
with Apps.Functions.Functions.Functions_Functions;
with Apps.Functions.Models.Functions_Models;
with Apps.Migrations.Functions.Migrations_Functions;
with Apps.Migrations.Models.Migrations_Models;
with Apps.Model.Functions.Model_Functions;
with Apps.Model.Models.Model_Models;
with Apps.Models.Functions.Models_Functions;
with Apps.Models.Models.Models_Models;
with Apps.OCPP.Functions.OCPP_Functions;
with Apps.OCPP.Models.OCPP_Models;
with Apps.OCPP.Triggers.OCPP_Triggers;
with Apps.Preview.Functions.Preview_Functions;
with Apps.Preview.Models.Preview_Models;
with Apps.Product.Functions.Product_Functions;
with Apps.Product.Models.Product_Models;
with Apps.Templates.Functions.Templates_Functions;
with Apps.Templates.Models.Templates_Models;
with Apps.Test.Functions.Test_Functions;
with Apps.Test.Models.Test_Models;
with Apps.Views.Functions.Views_Functions;
with Apps.Views.Models.Views_Models;
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

      Apps.App.Models.App_Models.Install (Conn);
      Apps.App.Functions.App_Functions.Install (Conn);

      Apps.Functions.Models.Functions_Models.Install (Conn);
      Apps.Functions.Functions.Functions_Functions.Install (Conn);

      Apps.Model.Models.Model_Models.Install (Conn);
      Apps.Model.Functions.Model_Functions.Install (Conn);

      Apps.Migrations.Models.Migrations_Models.Install (Conn);
      Apps.Migrations.Functions.Migrations_Functions.Install (Conn);

      Apps.Models.Models.Models_Models.Install (Conn);
      Apps.Models.Functions.Models_Functions.Install (Conn);

      Apps.Templates.Models.Templates_Models.Install (Conn);
      Apps.Templates.Functions.Templates_Functions.Install (Conn);

      Apps.Views.Models.Views_Models.Install (Conn);
      Apps.Views.Functions.Views_Functions.Install (Conn);

      Apps.Fixtures.Models.Fixtures_Models.Install (Conn);
      Apps.Fixtures.Functions.Fixtures_Functions.Install (Conn);

      Apps.Test.Models.Test_Models.Install (Conn);
      Apps.Test.Functions.Test_Functions.Install (Conn);

      Apps.OCPP.Models.OCPP_Models.Install (Conn);
      Apps.OCPP.Functions.OCPP_Functions.Install (Conn);
      Apps.OCPP.Triggers.OCPP_Triggers.Install (Conn);

      Apps.Product.Models.Product_Models.Install (Conn);

      Apps.Command.Models.Command_Models.Install (Conn);
      Apps.Command.Functions.Command_Functions.Install (Conn);

      Apps.Preview.Models.Preview_Models.Install (Conn);
      Apps.Preview.Functions.Preview_Functions.Install (Conn);

      Apps.Product.Functions.Product_Functions.Install (Conn);

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
