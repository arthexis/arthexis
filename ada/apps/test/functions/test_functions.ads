with Arthexis.ORM;

package Apps.Test.Functions.Test_Functions is
   --  SQL-callable function registrations for the optional Test app.

   procedure Install (Conn : in out Arthexis.ORM.Database_Connection);
   --  Register Test app SQL helper views.
end Apps.Test.Functions.Test_Functions;
